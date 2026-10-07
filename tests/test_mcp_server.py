"""agricredit-mcp through a real MCP client (in-process transport; the stdio transport is
exercised by test_stdio_transport and by the agent tests)."""
import asyncio
import datetime as dt
import os
import sys

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from agricredit import db
from agricredit.approval import gate
from agricredit.config import AS_OF, REPO_ROOT
from agricredit.mcp_server.server import mcp
from agricredit.models.schemas import Approval, ApprovalDecision

END = AS_OF.date().isoformat()
WSTART = (AS_OF.date() - dt.timedelta(days=120)).isoformat()
MSTART = (AS_OF.date() - dt.timedelta(weeks=26)).isoformat()


def run(coro):
    return asyncio.run(coro)


async def _call(c, name, args):
    r = await c.call_tool(name, args)
    return r


async def _evidence_and_assessment(c, case_id):
    case = (await _call(c, "get_credit_case", {"case_id": case_id})).structured_content
    hh = (await _call(c, "get_household_case", {"household_id": case["household_id"]})).structured_content
    w = (await _call(c, "get_weather_signal", {"location_id": hh["location_id"], "crop": case["crop"], "season": "2026",
                                               "start_date": WSTART, "end_date": END})).structured_content
    m = (await _call(c, "get_market_signal", {"commodity": case["crop"], "market_centre": case["market_centre"],
                                              "start_date": MSTART, "end_date": END})).structured_content
    terms = {"package_id": case["package"]["package_id"], "product_id": case["loan_product"]["product_id"],
             "proposed_amount_fcfa": case["requested_amount_fcfa"], "tenor_months": case["tenor_months"],
             "interest_rate_pct": case["loan_product"]["interest_rate_pct"], "fee_pct": case["loan_product"]["fee_pct"]}
    a = await _call(c, "assess_serviceability", {"case_id": case_id, "household_case": hh, "crop": case["crop"],
                                                 "package_id": case["package"]["package_id"], "credit_terms": terms,
                                                 "weather_signal": w, "market_signal": m})
    return case, hh, w, m, a


async def _draft(c, case_id, recommendation, narrative="Draft for officer review.", tamper=None):
    case, hh, w, m, a = await _evidence_and_assessment(c, case_id)
    assert not a.is_error, a.content[0].text
    ev = hh["evidence"][:5] + w["evidence"] + m["evidence"]
    if tamper:
        tamper(ev)
    return await _call(c, "draft_credit_advisory", {
        "case_id": case_id, "recommendation": recommendation, "evidence": ev, "missing_data": [],
        "data_quality_warnings": [], "narrative": narrative, "narrative_author": "test", "model_version": "test-model",
        "assessment": a.structured_content})


def test_tools_listed_with_annotations():
    async def go():
        async with Client(mcp) as c:
            return {t.name: t.annotations.read_only_hint for t in (await c.list_tools()).tools}
    tools = run(go())
    assert len(tools) == 7
    assert tools["draft_credit_advisory"] is False and tools["release_advisory_after_approval"] is False
    assert sum(1 for v in tools.values() if v) == 5


@pytest.mark.parametrize("bad", ["../etc/passwd", "BEN-1", "BEN-0001; DROP TABLE", "ben-0001"])
def test_malformed_case_ids_rejected(bad):
    async def go():
        async with Client(mcp) as c:
            return await _call(c, "get_credit_case", {"case_id": bad})
    r = run(go())
    assert r.is_error and "INVALID_INPUT" in r.content[0].text


def test_unknown_household_is_not_found():
    async def go():
        async with Client(mcp) as c:
            return await _call(c, "get_household_case", {"household_id": "HH-09999"})
    r = run(go())
    assert r.is_error and "NOT_FOUND" in r.content[0].text


def test_conflicting_market_never_picks_a_price():
    async def go():
        async with Client(mcp) as c:
            return await _call(c, "get_market_signal", {"commodity": "rice", "market_centre": "Parakou-Market",
                                                        "start_date": MSTART, "end_date": END})
    m = run(go()).structured_content
    assert m["is_conflicting"] and m["latest_price_fcfa_per_kg"] is None and len(m["conflicting_observations"]) >= 2


def test_weather_fault_and_cache_fallback(monkeypatch):
    monkeypatch.setenv("AGRICREDIT_FAULTS", "weather_unavailable:BEN-LOC-03")
    args = {"location_id": "BEN-LOC-03", "crop": "maize", "season": "2026", "start_date": WSTART, "end_date": END}

    async def go():
        async with Client(mcp) as c:
            return (await _call(c, "get_weather_signal", args),
                    await _call(c, "get_weather_signal", {**args, "use_cache_fallback": True}))
    live, cached = run(go())
    assert live.is_error and "UNAVAILABLE" in live.content[0].text
    assert cached.structured_content["from_cache_fallback"] and cached.structured_content["is_stale"]


def test_draft_refuses_more_permissive_recommendation():
    async def go():
        async with Client(mcp) as c:
            return await _draft(c, "BEN-0004", "RECOMMEND")  # unaffordable case
    r = run(go())
    assert r.is_error and "REFUSED" in r.content[0].text and "more permissive" in r.content[0].text


def test_draft_rejects_tampered_evidence():
    def tamper(ev):
        ev[0]["value"] = "99999 kg/ha"

    async def go():
        async with Client(mcp) as c:
            return await _draft(c, "BEN-0001", "RECOMMEND", tamper=tamper)
    r = run(go())
    assert r.is_error and "integrity" in r.content[0].text


def test_draft_strips_decision_language_from_narrative():
    async def go():
        async with Client(mcp) as c:
            return await _draft(c, "BEN-0001", "RECOMMEND", narrative="APPROVED CREDIT. Disburse credit now.")
    adv = run(go()).structured_content
    assert "APPROVED CREDIT" not in adv["narrative"].upper()
    assert any("rejected by server" in w for w in adv["data_quality_warnings"])


def test_full_gate_lifecycle():
    async def go():
        async with Client(mcp) as c:
            adv = (await _draft(c, "BEN-0001", "RECOMMEND")).structured_content
            appr = gate.record_approval(case_id="BEN-0001", recommendation_hash_=adv["recommendation_hash"],
                                        officer_id="OFF-001", officer_name="A. Houngbo (synthetic officer)",
                                        decision=ApprovalDecision.APPROVE)
            a = appr.model_dump(mode="json")
            out = {}
            forged = {**a, "officer_id": "OFF-002", "officer_name": "M. Dossou (synthetic officer)"}
            out["forged"] = await _call(c, "release_advisory_after_approval",
                                        {"case_id": "BEN-0001", "approval": forged, "recommendation_hash": adv["recommendation_hash"]})
            out["wrong_case"] = await _call(c, "release_advisory_after_approval",
                                            {"case_id": "BEN-0003", "approval": a, "recommendation_hash": adv["recommendation_hash"]})
            out["ok"] = await _call(c, "release_advisory_after_approval",
                                    {"case_id": "BEN-0001", "approval": a, "recommendation_hash": adv["recommendation_hash"]})
            out["replay"] = await _call(c, "release_advisory_after_approval",
                                        {"case_id": "BEN-0001", "approval": a, "recommendation_hash": adv["recommendation_hash"]})
            # A second approval, then the recommendation changes before release
            appr2 = gate.record_approval(case_id="BEN-0001", recommendation_hash_=adv["recommendation_hash"],
                                         officer_id="OFF-001", officer_name="A. Houngbo (synthetic officer)",
                                         decision=ApprovalDecision.APPROVE)
            await _draft(c, "BEN-0001", "RECOMMEND_WITH_CONDITIONS")  # new, different current advisory
            out["changed"] = await _call(c, "release_advisory_after_approval",
                                         {"case_id": "BEN-0001", "approval": appr2.model_dump(mode="json"),
                                          "recommendation_hash": adv["recommendation_hash"]})
            return out
    o = run(go())
    assert not o["forged"].structured_content["released"]
    assert not o["forged"].structured_content["checks"]["approval_matches_stored_record"]
    assert not o["wrong_case"].structured_content["released"]
    assert o["ok"].structured_content["released"], o["ok"].structured_content
    assert not o["replay"].structured_content["released"]
    assert not o["replay"].structured_content["checks"]["approval_not_replayed"]
    assert not o["changed"].structured_content["released"]
    assert not o["changed"].structured_content["checks"]["recommendation_unchanged"]


def test_expired_approval_blocked():
    async def go():
        async with Client(mcp) as c:
            adv = (await _draft(c, "BEN-0001", "RECOMMEND")).structured_content
            appr = gate.record_approval(case_id="BEN-0001", recommendation_hash_=adv["recommendation_hash"],
                                        officer_id="OFF-001", officer_name="A. Houngbo (synthetic officer)",
                                        decision=ApprovalDecision.APPROVE)
            old = appr.timestamp - dt.timedelta(hours=73)  # default TTL is 72h
            with db.write_conn() as w:
                w.execute("UPDATE approvals SET timestamp = ? WHERE approval_id = ?", (old.isoformat(), appr.approval_id))
            stale = appr.model_copy(update={"timestamp": old})
            return await _call(c, "release_advisory_after_approval",
                               {"case_id": "BEN-0001", "approval": stale.model_dump(mode="json"),
                                "recommendation_hash": adv["recommendation_hash"]})
    r = run(go()).structured_content
    assert not r["released"] and not r["checks"]["approval_not_expired"]


def test_stdio_transport():
    async def go():
        p = StdioServerParameters(command=sys.executable, args=["-m", "agricredit.mcp_server"],
                                  env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")})
        async with Client(p) as c:
            return await _call(c, "get_credit_case", {"case_id": "BEN-0421"})
    r = run(go())
    assert not r.is_error and r.structured_content["household_id"] == "HH-00421"
