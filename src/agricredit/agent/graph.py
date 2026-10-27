"""AgriCredit agent: an explicit LangGraph state machine.

START -> load_case -> validate_data -> plan_investigation -> get_household_evidence -> get_weather_evidence
      -> get_market_evidence -> quantitative_assessment -> contradiction_check -> evidence_sufficiency
      -> draft_advisory -> human_approval (interrupt) -> release | terminate -> finalize -> END
Any node can route to `refuse` (no advisory is drafted), which also ends in finalize.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import operator
from typing import Annotated, Any, Optional, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..audit.logger import log_event, utcnow
from ..config import AS_OF, get_settings
from ..policy import decide
from . import llm
from .toolbox import CURRENT

MARKET_CENTRES = ["Bohicon-Market", "Parakou-Market", "Glazoue-Market", "Dassa-Market", "Malanville-Market"]


def market_centres(policy: dict) -> list[str]:
    """Known market centres = every centre named in the policy's alternate-centre map (synthetic or real mode)."""
    alts = policy.get("market", {}).get("alternate_centres", {})
    known = set(alts) | set(alts.values())
    return MARKET_CENTRES if not known or known == set(MARKET_CENTRES) else sorted(known)


class AgentState(TypedDict, total=False):
    run_id: str
    case_id: str
    status: str
    refusal_reason: Optional[str]
    policy: dict
    case: dict
    plan: dict
    plan_source: str
    household: Optional[dict]
    weather: Optional[dict]
    weather_from_cache: bool
    market: Optional[dict]
    assessment: Optional[dict]
    contradictions: list[str]
    blocking_gaps: list[str]
    warnings: list[str]
    decision: dict
    advisory: Optional[dict]
    release: Optional[dict]
    officer_outcome: Optional[dict]
    llm_used: list[str]
    activity: Annotated[list[dict], operator.add]


def act(node: str, message: str, **extra) -> dict:
    return {"node": node, "message": message, "at": utcnow().isoformat(), **extra}


def tb():
    return CURRENT.get()


def _retries(state) -> int:
    return int(state["policy"]["tool_retry"]["max_attempts"])


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------

async def load_case(state: AgentState) -> dict:
    t = tb()
    pol = await t.read_policy("load_case")
    if not pol.ok:
        return {"status": "REFUSED", "refusal_reason": f"Decision policy could not be loaded ({pol.error}); failing closed.",
                "activity": [act("load_case", "Policy unavailable: failing closed", tool="filesystem.read_text_file")]}
    r = await t.call("agricredit", "get_credit_case", {"case_id": state["case_id"]}, node="load_case")
    if not r.ok:
        return {"policy": pol.data, "status": "REFUSED", "refusal_reason": f"Case cannot be loaded: {r.error}",
                "activity": [act("load_case", f"Case not loadable: {r.code}", tool="agricredit.get_credit_case")]}
    c = r.data
    return {"policy": pol.data, "case": c, "status": "RUNNING", "blocking_gaps": [], "warnings": [], "contradictions": [],
            "llm_used": [], "weather_from_cache": False,
            "activity": [act("load_case", f"Loaded policy {pol.data['policy_version']} via filesystem MCP server",
                             tool="filesystem.read_text_file"),
                         act("load_case", f"Case {c['case_id']}: {c['package']['name']}, {c['requested_amount_fcfa']:,.0f} FCFA "
                                          f"over {c['tenor_months']} months for household {c['household_id']}",
                             tool="agricredit.get_credit_case")]}


async def validate_data(state: AgentState) -> dict:
    c = state["case"]
    problems = []
    if c["package"]["crop"] != c["crop"]:
        problems.append(f"package crop {c['package']['crop']} differs from case crop {c['crop']}")
    if c["requested_amount_fcfa"] <= 0 or c["tenor_months"] <= 0:
        problems.append("non-positive amount or tenor")
    if c["market_centre"] not in market_centres(state["policy"]):
        problems.append(f"unknown market centre {c['market_centre']}")
    if problems:
        return {"status": "REFUSED", "refusal_reason": "Case request is invalid: " + "; ".join(problems),
                "activity": [act("validate_data", "Case request invalid")]}
    return {"activity": [act("validate_data", "Case request is internally consistent")]}


def _default_plan(state) -> dict:
    p, c = state["policy"], state["case"]
    return {"investigation_focus": ["repayment track record and record freshness", f"{c['crop']} yield stability",
                                    "season-to-date rainfall anomaly", f"current {c['crop']} price at {c['market_centre']}",
                                    "repayment capacity under combined shock"],
            "weather_lookback_days": p["weather"]["lookback_days"], "market_lookback_weeks": p["market"]["lookback_weeks"],
            "alternate_market_centre": p["market"]["alternate_centres"].get(c["market_centre"]),
            "risks_to_check": ["stale records", "conflicting market feeds", "drought", "existing obligations"]}


async def plan_investigation(state: AgentState) -> dict:
    t, c, p = tb(), state["case"], state["policy"]
    default = _default_plan(state)
    tools = [f"{srv}.{x['name']}{' (action)' if not x['read_only'] else ''}: {x['description']}"
             for srv, lst in t.tool_catalogue.items() for x in lst]
    prompt = (
        "Plan the evidence investigation for this input-credit case. The tool execution order is fixed by the "
        "workflow; you choose the parameters and what to scrutinise.\n\n"
        f"CASE: {json.dumps({k: c[k] for k in ['case_id', 'crop', 'season', 'market_centre', 'requested_amount_fcfa', 'tenor_months']})}\n"
        f"PACKAGE: {c['package']['name']} costing {c['package']['cost_fcfa']:,.0f} FCFA; LOAN: {c['loan_product']['name']}\n"
        f"AVAILABLE MCP TOOLS:\n- " + "\n- ".join(tools) + "\n\n"
        f"Allowed alternate market centres: {market_centres(p)}.\n"
        'Return JSON only: {"investigation_focus": [3-6 short strings], "weather_lookback_days": int 30-180, '
        '"market_lookback_weeks": int 8-52, "alternate_market_centre": one allowed centre different from the case centre, '
        '"risks_to_check": [2-6 short strings]}'
    )
    try:
        raw = llm.chat([{"role": "system", "content": llm.SYSTEM}, {"role": "user", "content": prompt}], purpose="plan",
                       json_mode=True, run_id=state["run_id"], case_id=state["case_id"], node="plan_investigation",
                       stub_response=json.dumps(default))
        plan = llm.parse_json(raw)
        clean = {
            "investigation_focus": [str(x)[:120] for x in plan.get("investigation_focus", [])][:6] or default["investigation_focus"],
            "weather_lookback_days": min(180, max(30, int(plan.get("weather_lookback_days", default["weather_lookback_days"])))),
            "market_lookback_weeks": min(52, max(8, int(plan.get("market_lookback_weeks", default["market_lookback_weeks"])))),
            "alternate_market_centre": plan.get("alternate_market_centre") if plan.get("alternate_market_centre") in market_centres(p)
            and plan.get("alternate_market_centre") != c["market_centre"] else default["alternate_market_centre"],
            "risks_to_check": [str(x)[:120] for x in plan.get("risks_to_check", [])][:6] or default["risks_to_check"],
        }
        src = get_settings().model_version
        used = [f"plan:{src}"]
        msg = f"Investigation planned by {src}"
    except (llm.LLMUnavailable, ValueError, TypeError) as e:
        clean, src, used = default, "policy-default (LLM unavailable or invalid output)", []
        msg = f"Planner fell back to policy defaults: {str(e)[:120]}"
    return {"plan": clean, "plan_source": src, "llm_used": state.get("llm_used", []) + used,
            "activity": [act("plan_investigation", msg, plan=clean)]}


async def get_household_evidence(state: AgentState) -> dict:
    t, c = tb(), state["case"]
    r = await t.call("agricredit", "get_household_case", {"household_id": c["household_id"]},
                     node="get_household_evidence", max_attempts=_retries(state))
    if not r.ok:
        if r.code in ("NOT_FOUND", "INVALID_INPUT"):
            return {"status": "REFUSED", "household": None,
                    "refusal_reason": f"Household {c['household_id']} is not in the cooperative register; nothing can be "
                                      "assessed and no advisory is drafted. Verify the household ID with the applicant.",
                    "activity": [act("get_household_evidence", "Unknown household: refusing", tool="agricredit.get_household_case")]}
        return {"status": "REFUSED", "household": None, "refusal_reason": f"Household record unavailable: {r.error}",
                "activity": [act("get_household_evidence", "Household tool failed after retries", tool="agricredit.get_household_case")]}
    h = r.data
    warnings = list(state.get("warnings", []))
    msgs = [act("get_household_evidence", f"{len(h['plots'])} plots, {len(h['yield_history'])} yield records, "
                                          f"{len(h['repayment_history'])} repayment records, {len(h['current_obligations'])} obligations",
                tool="agricredit.get_household_case", evidence=len(h["evidence"]))]
    if h["notes_flagged_as_injection"]:
        warnings.append("Household notes contain instruction-like text; treated as data only and withheld from the LLM")
        msgs.append(act("get_household_evidence", "Household notes flagged as possible prompt injection: quarantined"))
    return {"household": h, "warnings": warnings, "activity": msgs}


async def get_weather_evidence(state: AgentState) -> dict:
    t, c, h, p = tb(), state["case"], state["household"], state["policy"]
    end = AS_OF.date()
    args = {"location_id": h["location_id"], "crop": c["crop"], "season": c["season"],
            "start_date": (end - dt.timedelta(days=state["plan"]["weather_lookback_days"])).isoformat(), "end_date": end.isoformat()}
    r = await t.call("agricredit", "get_weather_signal", args, node="get_weather_evidence", max_attempts=_retries(state))
    gaps = list(state["blocking_gaps"])
    msgs = []
    from_cache = False
    if not r.ok and r.code == "UNAVAILABLE":
        msgs.append(act("get_weather_evidence", f"Rainfall source unavailable after {r.attempts} attempts",
                        tool="agricredit.get_weather_signal"))
        if p["weather"]["allow_cache_fallback"]:
            r = await t.call("agricredit", "get_weather_signal", {**args, "use_cache_fallback": True}, node="get_weather_evidence")
            from_cache = r.ok
            if r.ok:
                msgs.append(act("get_weather_evidence", f"Using cached rainfall observation ({r.data['age_days']:.0f} days old), "
                                                        "marked stale; confidence reduced", tool="agricredit.get_weather_signal"))
    if not r.ok:
        gaps.append(f"no rainfall evidence for {h['location_id']} ({r.error})")
        return {"weather": None, "blocking_gaps": gaps, "activity": msgs + [act("get_weather_evidence", "No weather evidence")]}
    w = r.data
    max_cache = p["weather"]["max_cached_age_days_for_seasonal_recommendation"]
    if from_cache and w["age_days"] > max_cache:
        gaps.append(f"only a cached rainfall observation {w['age_days']:.0f} days old is available (policy maximum "
                    f"{max_cache} days for a seasonal recommendation)")
    elif w["is_stale"] and not from_cache:
        gaps.append(f"rainfall observation is {w['age_days']:.0f} days old")
    msgs.append(act("get_weather_evidence", f"Rainfall {w['rainfall_mm']} mm, {w['rainfall_anomaly_pct']:+.1f}% vs baseline"
                                            f"{', DROUGHT' if w['drought_indicator'] else ''}", tool="agricredit.get_weather_signal"))
    return {"weather": w, "weather_from_cache": from_cache, "blocking_gaps": gaps, "activity": msgs}


async def get_market_evidence(state: AgentState) -> dict:
    t, c, p, plan = tb(), state["case"], state["policy"], state["plan"]
    end = AS_OF.date()
    start = (end - dt.timedelta(weeks=plan["market_lookback_weeks"])).isoformat()

    async def fetch(centre):
        return await t.call("agricredit", "get_market_signal", {"commodity": c["crop"], "market_centre": centre,
                                                                "start_date": start, "end_date": end.isoformat()},
                            node="get_market_evidence", max_attempts=_retries(state))

    gaps, warnings, msgs = list(state["blocking_gaps"]), list(state["warnings"]), []
    r = await fetch(c["market_centre"])
    if not r.ok:
        gaps.append(f"no {c['crop']} price series for {c['market_centre']} ({r.error})")
        return {"market": None, "blocking_gaps": gaps, "activity": [act("get_market_evidence", "No market evidence")]}
    m = r.data
    if m["is_conflicting"]:
        obs = ", ".join(f"{o['price']} FCFA/kg ({o['source']})" for o in m["conflicting_observations"])
        gaps.append(f"conflicting same-day {c['crop']} prices at {c['market_centre']}: {obs}; the agent does not choose or "
                    "average between feeds")
        msgs.append(act("get_market_evidence", "Market feeds disagree: no current price will be assumed",
                        tool="agricredit.get_market_signal"))
        return {"market": m, "blocking_gaps": gaps, "activity": msgs}
    if m["is_stale"]:
        msgs.append(act("get_market_evidence", f"{c['crop']} price at {c['market_centre']} is {m['age_days']:.0f} days old: "
                                               "attempting bounded recovery", tool="agricredit.get_market_signal"))
        alt = plan.get("alternate_market_centre")
        recovered = False
        if p["market"]["stale_recovery"] == "alternate_centre" and alt and p["market"]["max_alternate_centres"] >= 1:
            r2 = await fetch(alt)
            if r2.ok and not r2.data["is_stale"] and not r2.data["is_conflicting"]:
                m, recovered = r2.data, True
                warnings.append(f"{c['crop']} price taken from alternate centre {alt} because {c['market_centre']} data is stale")
                msgs.append(act("get_market_evidence", f"Recovered a current price from {alt}", tool="agricredit.get_market_signal"))
            else:
                msgs.append(act("get_market_evidence", f"Alternate centre {alt} is also unusable", tool="agricredit.get_market_signal"))
        if not recovered:
            gaps.append(f"no current {c['crop']} price: latest observation at {c['market_centre']} is {m['age_days']:.0f} days "
                        f"old and the alternate centre is also stale or unavailable")
            return {"market": m, "blocking_gaps": gaps, "warnings": warnings, "activity": msgs}
    seasonal = m.get("seasonal_anomaly_pct")
    if seasonal is not None:
        # With a multi-year history, compare with the same month in earlier years, so a normal post-harvest dip is not
        # mistaken for a shock (and a real collapse is not hidden by an already-low recent median).
        month = dt.datetime.fromisoformat(str(m["observation_timestamp"])).strftime("%B")
        if seasonal <= p["market"]["price_drop_warning_pct"]:
            warnings.append(f"latest {c['crop']} price is {seasonal:+.1f}% vs the usual {month} price ({m['seasonal_years']} median)")
        detail = f"{seasonal:+.1f}% vs the usual {month} price"
    else:
        if m["price_anomaly_pct"] is not None and m["price_anomaly_pct"] <= p["market"]["price_drop_warning_pct"]:
            warnings.append(f"latest {c['crop']} price is {m['price_anomaly_pct']:+.1f}% vs the {plan['market_lookback_weeks']}-week median")
        detail = f"{m['price_anomaly_pct']:+.1f}% vs median"
    msgs.append(act("get_market_evidence", f"{c['crop']} {m['latest_price_fcfa_per_kg']} FCFA/kg at {m['market_centre']} "
                                           f"({detail})", tool="agricredit.get_market_signal"))
    return {"market": m, "blocking_gaps": gaps, "warnings": warnings, "activity": msgs}


async def quantitative_assessment(state: AgentState) -> dict:
    t, c, h, w, m = tb(), state["case"], state["household"], state.get("weather"), state.get("market")
    if not (h and w and m and m.get("latest_price_fcfa_per_kg") is not None and not m.get("is_stale")):
        return {"assessment": None, "activity": [act("quantitative_assessment", "Skipped: required evidence is missing")]}
    terms = {"package_id": c["package"]["package_id"], "product_id": c["loan_product"]["product_id"],
             "proposed_amount_fcfa": c["requested_amount_fcfa"], "tenor_months": c["tenor_months"],
             "interest_rate_pct": c["loan_product"]["interest_rate_pct"], "fee_pct": c["loan_product"]["fee_pct"]}
    r = await t.call("agricredit", "assess_serviceability",
                     {"case_id": c["case_id"], "household_case": h, "crop": c["crop"], "package_id": c["package"]["package_id"],
                      "credit_terms": terms, "weather_signal": w, "market_signal": m}, node="quantitative_assessment")
    if not r.ok:
        return {"assessment": None, "blocking_gaps": state["blocking_gaps"] + [f"assessment refused: {r.error}"],
                "activity": [act("quantitative_assessment", f"Assessment refused: {r.error[:120]}", tool="agricredit.assess_serviceability")]}
    a = r.data
    top = sorted(a["feature_contributions"].items(), key=lambda kv: -abs(kv[1]))[:3]
    return {"assessment": a, "activity": [act(
        "quantitative_assessment",
        f"PD {a['probability_of_default']:.3f} (score {a['risk_score']}, {a['risk_band']}); capacity {a['base_repayment_capacity_ratio']:.2f}x "
        f"base / {a['stress_repayment_capacity_ratio']:.2f}x combined shock. Largest risk drivers: "
        + ", ".join(f"{k} {v:+.2f}" for k, v in top), tool="agricredit.assess_serviceability")]}


async def contradiction_check(state: AgentState) -> dict:
    h, w, m, a = state.get("household"), state.get("weather"), state.get("market"), state.get("assessment")
    found, warnings = [], list(state["warnings"])
    if m and m.get("is_conflicting"):
        found.append("market feeds report materially different same-day prices")
    if h:
        plot_crops = {p["crop"] for p in h["plots"]}
        for y in h["yield_history"]:
            if y["crop"] not in plot_crops:
                found.append(f"yield record {y['record_id']} is for {y['crop']}, which no plot grows")
        for r in h["repayment_history"]:
            if r["defaulted"] and r["paid_date"]:
                found.append(f"repayment {r['loan_id']} is marked defaulted but has a paid date")
        if a and a["risk_band"] == "LOW" and sum(r["defaulted"] for r in h["repayment_history"]) >= 2:
            found.append("model risk band is LOW despite two or more recorded defaults")
    if w and (w["rainfall_anomaly_pct"] <= -20) != w["drought_indicator"]:
        found.append("drought flag inconsistent with rainfall anomaly")
    warnings += [f"contradiction: {f}" for f in found if "market feeds" not in f]
    return {"contradictions": found, "warnings": warnings,
            "activity": [act("contradiction_check", "No contradictions found" if not found else f"{len(found)} contradiction(s): " + "; ".join(found))]}


async def evidence_sufficiency(state: AgentState) -> dict:
    h, p, a, w = state.get("household"), state["policy"], state.get("assessment"), state.get("weather")
    gaps, warnings = list(state["blocking_gaps"]), list(state["warnings"])
    if h:
        f = h["data_freshness"]
        if f["repayment_records_missing"] and p["repayment"]["require_history"]:
            gaps.append("no repayment history on file (first-time applicant): a cooperative reference, savings-group record "
                        "or guarantor is needed before a credit recommendation")
        elif f["repayment_is_stale"]:
            warnings.append(f"latest repayment record is {f['repayment_record_age_days']:.0f} days old: verify current status")
        if f["yield_seasons_observed"] < p["yield"]["min_seasons"]:
            gaps.append(f"only {f['yield_seasons_observed']} season(s) of yield records")
    if w and w["drought_indicator"]:
        warnings.append(f"drought conditions this season ({w['rainfall_anomaly_pct']:+.1f}% rainfall vs baseline)")
    if state.get("weather_from_cache") and w and not any("cached rainfall" in g for g in gaps):
        warnings.append(f"rainfall evidence is a cached observation {w['age_days']:.0f} days old")
    if a is None and not gaps:
        gaps.append("quantitative assessment could not be completed")
    gaps, warnings = list(dict.fromkeys(gaps)), list(dict.fromkeys(warnings))
    verdict = "SUFFICIENT" if not gaps else f"INSUFFICIENT ({len(gaps)} gap(s))"
    return {"blocking_gaps": gaps, "warnings": warnings,
            "activity": [act("evidence_sufficiency", f"Evidence {verdict}; {len(warnings)} warning(s)", gaps=gaps, warnings=warnings)]}


# --------------------------------------------------------------------------
# Narrative claim guard: small models can restate the right numbers and still draw the wrong
# conclusion ("-19% suggests drought" when the drought rule says no). Key findings are computed
# deterministically, handed to the model as the only allowed conclusions, then checked in its text.
# --------------------------------------------------------------------------

_NEGATION = re.compile(r"\b(no|not|without|isn't|wasn't|does not|did not|below the .{0,20}threshold|short of|above the .{0,20}threshold"
                       r"|pas|aucune?|sans|ni|n['’]|ne)\b", re.I)
_DROUGHT = re.compile(r"drought|s[ée]cheresse", re.I)
_CROSSCHECK = re.compile(r"cross.?check|contre.?v[ée]rification|v[ée]rification ind[ée]pendante|source ind[ée]pendante|seconde source", re.I)
_CONFIRMS = re.compile(r"\b(confirm|support|corroborat|agree|concord|appui)", re.I)
_CAN_REPAY = re.compile(r"(potential|able|capacity|sufficient|enough) to (re)?pay|can (re)?pay|will (re)?pay"
                        r"|capable de rembourser|peut rembourser|pourra rembourser|en mesure de rembourser"
                        r"|suffisante? pour rembourser|capacit[ée] (de|à) rembourser le pr[êe]t", re.I)
_HH_YIELD = re.compile(r"(household|m[ée]nage)[^.]{0,80}\b(has|had|have|with|achieved|produces?|a|obtient|produit|avec)\b"
                       r"[^.]{0,40}(yield|rendement)", re.I)


def key_findings(state) -> dict:
    a, w = state.get("assessment"), state.get("weather")
    out: dict = {"statements": []}
    if w:
        anom, drought = float(w["rainfall_anomaly_pct"]), bool(w["drought_indicator"])
        out["drought"] = drought
        out["statements"].append(
            f"DROUGHT: yes. Rainfall is {anom:+.1f}% vs its baseline, at or beyond the -20% drought threshold." if drought else
            f"DROUGHT: no. Rainfall is {anom:+.1f}% vs its baseline, which does not reach the -20% drought threshold.")
        for e in w.get("evidence", []):
            if e["evidence_id"].startswith("weather_crosscheck:"):
                m = re.search(r"\(([+-]?\d+(?:\.\d+)?)% vs its own baseline", e["value"])
                if m:
                    other = float(m.group(1))
                    agree = abs(other - anom) <= 15 and (other < 0) == (anom < 0)
                    out["sources_agree"] = agree
                    out["statements"].append(
                        f"RAINFALL CROSS-CHECK: the independent source shows {other:+.1f}%, so the two sources "
                        + ("broadly agree." if agree else "DISAGREE; do not say the cross-check confirms anything."))
    if a:
        cap = float(a["base_repayment_capacity_ratio"])
        out["can_repay"] = cap >= 1.0
        out["statements"].append(
            f"REPAYMENT: base capacity {cap:.2f}x is " + ("at least 1.0x: expected cash covers the loan in a normal season."
                                                         if cap >= 1.0 else "below 1.0x: expected cash does NOT cover the loan, even in a normal season."))
    commune = [e for e in state["household"]["evidence"] if str(e["evidence_id"]).startswith("commune_yield:")]
    out["commune_yields"] = {m.group(1).replace(",", "") for e in commune for m in [re.match(r"([\d,]+) kg/ha", e["value"])] if m}
    if commune:
        out["statements"].append("YIELDS: 'Official ... yield' items are commune averages from government statistics, "
                                 "not this household's own yields.")
    return out


def unsupported_claims(text: str, findings: dict) -> list[str]:
    problems = []
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    if findings.get("drought") is False:
        for s_ in sentences:
            if _DROUGHT.search(s_) and not _NEGATION.search(s_):
                problems.append("claims drought, but the drought indicator is off")
                break
    if findings.get("sources_agree") is False:
        for s_ in sentences:
            if _CROSSCHECK.search(s_) and _CONFIRMS.search(s_) and not _NEGATION.search(s_):
                problems.append("says the rainfall cross-check confirms, but the sources disagree")
                break
    if findings.get("can_repay") is False:
        for s_ in sentences:
            if _CAN_REPAY.search(s_) and not _NEGATION.search(s_):
                problems.append("suggests the household can repay, but base capacity is below 1.0x")
                break
    if findings.get("commune_yields"):
        for s_ in sentences:
            nums = {str(int(n)) for n in llm._numbers_in(s_) if float(n).is_integer()}
            if _HH_YIELD.search(s_) and nums & findings["commune_yields"]:
                problems.append("presents the commune's official average yield as this household's own yield")
                break
    return problems


FR_RECOMMENDATION = {
    "RECOMMEND": "RECOMMANDER", "RECOMMEND_WITH_CONDITIONS": "RECOMMANDER SOUS CONDITIONS",
    "REDUCE_CREDIT_AMOUNT": "RÉDUIRE LE MONTANT DU CRÉDIT", "REQUEST_MORE_EVIDENCE": "DEMANDER DES PREUVES COMPLÉMENTAIRES",
    "NO_RECOMMENDATION": "PAS DE RECOMMANDATION",
}


def _fr(x: float, nd: int = 2) -> str:
    return f"{x:,.{nd}f}".replace(",", "\u202f").replace(".", ",")


def _template_narrative_fr(state, decision, warnings) -> str:
    a, c = state.get("assessment"), state["case"]
    rec = decision["recommendation"].value
    lines = [f"Projet d'avis soumis à la décision de l'agent pour le dossier {c['case_id']} "
             f"({c['package']['name']}, {_fr(c['requested_amount_fcfa'], 0)} FCFA).",
             f"Recommandation : {FR_RECOMMENDATION.get(rec, rec)}."]
    if a:
        lines.append(f"Probabilité de défaut {_fr(a['probability_of_default'], 3)} ; capacité de remboursement "
                     f"{_fr(a['base_repayment_capacity_ratio'])} fois en situation normale et "
                     f"{_fr(a['stress_repayment_capacity_ratio'])} fois en cas de choc combiné (rendement et prix en baisse).")
        if a["base_repayment_capacity_ratio"] < 1:
            lines.append("La récolte attendue ne couvre pas le remboursement, même en année normale.")
    if decision.get("suggested_amount_fcfa"):
        lines.append(f"Montant réduit proposé : environ {_fr(decision['suggested_amount_fcfa'], 0)} FCFA.")
    if warnings:
        lines.append(f"Points d'attention ({len(warnings)}) : voir la liste des avertissements.")
    lines.append("Aucun message n'est envoyé sans l'approbation d'un agent nommé.")
    return " ".join(lines)


def _template_narrative(state, decision, warnings) -> str:
    if get_settings().advisory_language == "fr":
        return _template_narrative_fr(state, decision, warnings)
    a, c = state.get("assessment"), state["case"]
    lines = [f"Draft for officer review on case {c['case_id']} ({c['package']['name']}, {c['requested_amount_fcfa']:,.0f} FCFA).",
             f"Recommendation: {decision['recommendation'].value}. {decision['rationale']}"]
    if a:
        lines.append(f"Probability of default {a['probability_of_default']:.3f}; repayment capacity "
                     f"{a['base_repayment_capacity_ratio']:.2f}x base and {a['stress_repayment_capacity_ratio']:.2f}x under the combined shock.")
    if warnings:
        lines.append("Warnings: " + "; ".join(warnings) + ".")
    lines.append(f"Officer action: {decision['officer_action']}")
    return " ".join(lines)


async def draft_advisory(state: AgentState) -> dict:
    t, c, a, h = tb(), state["case"], state.get("assessment"), state["household"]
    warnings = list(state["warnings"])
    decision = decide(a, state["blocking_gaps"], warnings, c["requested_amount_fcfa"], state["policy"],
                      state.get("weather_from_cache", False))
    evidence = list(h["evidence"]) + (state["weather"]["evidence"] if state.get("weather") else []) \
        + (state["market"]["evidence"] if state.get("market") else [])
    facts = {
        "case_id": c["case_id"], "product": c["package"]["name"], "requested_amount_fcfa": c["requested_amount_fcfa"],
        "tenor_months": c["tenor_months"], "recommendation": decision["recommendation"].value,
        "suggested_amount_fcfa": decision["suggested_amount_fcfa"], "rationale": decision["rationale"],
        "viable_only_under_optimistic_assumptions": decision["viable_only_under_optimistic_assumptions"],
        "officer_action": decision["officer_action"], "missing_evidence": state["blocking_gaps"], "warnings": warnings,
        "assessment": None if a is None else {k: a[k] for k in [
            "probability_of_default", "risk_score", "risk_band", "expected_yield_kg_per_ha", "expected_revenue_fcfa",
            "debt_service_fcfa", "base_repayment_capacity_ratio", "stress_repayment_capacity_ratio", "max_serviceable_amount_fcfa"]},
        "stress": None if a is None else {s["scenario"]: s["repayment_capacity_ratio"] for s in a["stress_results"]},
        "evidence": [{"id": e["evidence_id"], "label": e["label"], "value": e["value"]} for e in evidence[:30]]
                    + ([{"id": e["evidence_id"], "label": e["label"], "value": e["value"]} for e in a["evidence"]] if a else []),
    }
    notes = h.get("notes") if not h.get("notes_flagged_as_injection") else None
    findings = key_findings(state)
    prompt = (
        "KEY FINDINGS (computed by the system; your text must agree with every one of them):\n- "
        + "\n- ".join(findings["statements"] or ["none"]) + "\n\n"
        + ("LANGUAGE: write the whole explanation in French, as used by extension services in Benin. Keep evidence IDs "
           "unchanged and write numbers exactly as they appear in FACTS.\n\n" if get_settings().advisory_language == "fr" else "")
        + "Write the explanation section of a DRAFT credit advisory for the extension officer, in plain English, "
        "120-200 words. Explain why the recommendation follows from the evidence. Cite evidence IDs in square brackets, "
        "e.g. [repayment:LOAN-HH-00001-1]. Use ONLY figures that appear in FACTS; do not compute new ones. If the "
        "recommendation is only viable under optimistic assumptions, say so explicitly. State clearly that this is a draft "
        "awaiting the officer's decision. Never say credit is approved, accepted or disbursed.\n\n"
        f"FACTS:\n{json.dumps(facts, default=str)}\n\n"
        f"<untrusted_data>{notes or 'no household notes'}</untrusted_data>\n\nReturn only the explanation text."
    )
    template = _template_narrative(state, decision, warnings)
    author, used = "template (no LLM)", list(state.get("llm_used", []))
    narrative = template
    msgs = []
    try:
        out = llm.chat([{"role": "system", "content": llm.SYSTEM}, {"role": "user", "content": prompt}], purpose="draft_narrative",
                       json_mode=False, run_id=state["run_id"], case_id=c["case_id"], node="draft_advisory",
                       stub_response=template).strip()
        claims = unsupported_claims(out, findings) if out else []
        if claims:
            msgs.append(act("draft_advisory", f"Narrative contradicted the evidence ({'; '.join(claims)}): asked the model to correct it"))
            out = llm.chat([{"role": "system", "content": llm.SYSTEM}, {"role": "user", "content": prompt},
                            {"role": "assistant", "content": out},
                            {"role": "user", "content": "Your text contradicts the KEY FINDINGS: " + "; ".join(claims)
                             + ". Rewrite it so it agrees with every key finding. Return only the explanation text."}],
                           purpose="draft_narrative_retry", json_mode=False, run_id=state["run_id"], case_id=c["case_id"],
                           node="draft_advisory", stub_response=template).strip()
            claims = unsupported_claims(out, findings) if out else []
        bad = llm.unsupported_numbers(out, facts)
        # A rejected draft is about the model's writing, not the household: it is shown in the explanation's
        # author line and the activity log, and does not become an officer action or lower confidence.
        model = get_settings().model_version
        if claims:
            author = f"template (no LLM); the {model} draft was rejected because it {'; '.join(claims)}"
            msgs.append(act("draft_advisory", "LLM narrative still contradicted the evidence: template used"))
        elif not out:
            msgs.append(act("draft_advisory", "LLM returned empty text: template used"))
        elif bad:
            author = f"template (no LLM); the {model} draft was rejected for figures not in the evidence ({bad[:4]})"
            msgs.append(act("draft_advisory", f"LLM narrative rejected for unsupported figures {bad[:4]}: template used"))
        else:
            narrative, author = out, get_settings().model_version
            used.append(f"narrative:{author}")
    except llm.LLMUnavailable as e:
        warnings.append("LLM unavailable: explanation is a deterministic template")
        msgs.append(act("draft_advisory", f"LLM unavailable ({str(e)[:80]}): template used"))

    r = await t.call("agricredit", "draft_credit_advisory", {
        "case_id": c["case_id"], "recommendation": decision["recommendation"].value, "evidence": evidence,
        "missing_data": state["blocking_gaps"], "data_quality_warnings": warnings, "narrative": narrative,
        "narrative_author": author, "model_version": get_settings().model_version, "assessment": a,
        "weather_from_cache": state.get("weather_from_cache", False)}, node="draft_advisory")
    if not r.ok:
        return {"status": "ERROR", "refusal_reason": f"Draft refused by server: {r.error}", "decision": _jsonable(decision),
                "activity": msgs + [act("draft_advisory", "Server refused the draft", tool="agricredit.draft_credit_advisory")]}
    adv = r.data
    log_event(run_id=state["run_id"], case_id=c["case_id"], event_type="awaiting_officer_approval",
              details={"recommendation": adv["recommendation"], "recommendation_hash": adv["recommendation_hash"],
                       "evidence_items": len(adv["evidence"]), "narrative_author": author})
    return {"advisory": adv, "decision": _jsonable(decision), "status": "AWAITING_APPROVAL", "llm_used": used,
            "warnings": warnings,
            "activity": msgs + [act("draft_advisory", f"Draft stored: {adv['recommendation']} "
                                                      f"(hash {adv['recommendation_hash'][:12]}...), {len(adv['evidence'])} cited evidence items. "
                                                      "Waiting for a named officer.", tool="agricredit.draft_credit_advisory")]}


def _jsonable(d: dict) -> dict:
    return {k: (v.value if hasattr(v, "value") else v) for k, v in d.items()}


async def human_approval(state: AgentState) -> dict:
    decision = interrupt({"case_id": state["case_id"], "recommendation": state["advisory"]["recommendation"],
                          "recommendation_hash": state["advisory"]["recommendation_hash"],
                          "message": "A named officer must approve or reject this draft. The agent cannot proceed alone."})
    return {"officer_outcome": decision,
            "activity": [act("human_approval", f"Officer input received: {decision.get('action')}")]}


async def release(state: AgentState) -> dict:
    t, o = tb(), state["officer_outcome"]
    approval = o.get("approval")
    r = await t.call("agricredit", "release_advisory_after_approval",
                     {"case_id": state["case_id"], "approval": approval,
                      "recommendation_hash": o.get("recommendation_hash") or state["advisory"]["recommendation_hash"]},
                     node="release")
    if not r.ok:
        res = {"released": False, "reason": r.error, "checks": {}}
    else:
        res = r.data
    return {"release": res, "status": "RELEASED" if res["released"] else "RELEASE_BLOCKED",
            "activity": [act("release", ("Released to sandbox outbox" if res["released"] else "Release BLOCKED: " + res["reason"]),
                             tool="agricredit.release_advisory_after_approval")]}


async def terminate(state: AgentState) -> dict:
    o = state.get("officer_outcome") or {}
    return {"status": "TERMINATED_BY_OFFICER",
            "activity": [act("terminate", f"Officer ended the case without release: {o.get('reason', 'no reason given')}")]}


async def refuse(state: AgentState) -> dict:
    log_event(run_id=state["run_id"], case_id=state["case_id"], event_type="agent_refused",
              details={"reason": state.get("refusal_reason"), "status": state.get("status")})
    return {"activity": [act("refuse", state.get("refusal_reason") or "Refused")]}


async def finalize(state: AgentState) -> dict:
    t = tb()
    adv = state.get("advisory") or {}
    summary = {
        "run_id": state["run_id"], "case_id": state["case_id"], "status": state.get("status"),
        "refusal_reason": state.get("refusal_reason"), "model_version": get_settings().model_version,
        "llm_used": state.get("llm_used", []), "plan_source": state.get("plan_source"),
        "recommendation": adv.get("recommendation"), "recommendation_hash": adv.get("recommendation_hash"),
        "blocking_gaps": state.get("blocking_gaps", []), "warnings": state.get("warnings", []),
        "release": state.get("release"), "synthetic_data": True, "exported_at": utcnow().isoformat(),
    }
    r = await t.write_audit_export(f"{state['run_id']}.json", summary, node="finalize")
    log_event(run_id=state["run_id"], case_id=state["case_id"], event_type="run_finished",
              details={**{k: summary[k] for k in ["status", "recommendation", "model_version", "llm_used"]},
                       "audit_export_written": r.ok})
    return {"activity": [act("finalize", f"Run finished: {summary['status']}; audit summary written via filesystem MCP "
                                         f"({'ok' if r.ok else 'FAILED'})", tool="filesystem.write_file")]}


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------

def _ok(next_node: str):
    def route(state: AgentState) -> str:
        return "refuse" if state.get("status") in ("REFUSED", "ERROR") else next_node
    return route


def build_graph(checkpointer: Any = None):
    g = StateGraph(AgentState)
    for name, fn in [("load_case", load_case), ("validate_data", validate_data), ("plan_investigation", plan_investigation),
                     ("get_household_evidence", get_household_evidence), ("get_weather_evidence", get_weather_evidence),
                     ("get_market_evidence", get_market_evidence), ("quantitative_assessment", quantitative_assessment),
                     ("contradiction_check", contradiction_check), ("evidence_sufficiency", evidence_sufficiency),
                     ("draft_advisory", draft_advisory), ("human_approval", human_approval), ("release", release),
                     ("terminate", terminate), ("refuse", refuse), ("finalize", finalize)]:
        g.add_node(name, fn)
    g.add_edge(START, "load_case")
    chain = ["load_case", "validate_data", "plan_investigation", "get_household_evidence", "get_weather_evidence",
             "get_market_evidence", "quantitative_assessment", "contradiction_check", "evidence_sufficiency",
             "draft_advisory", "human_approval"]
    for a, b in zip(chain, chain[1:]):
        g.add_conditional_edges(a, _ok(b), [b, "refuse"])
    g.add_conditional_edges("human_approval",
                            lambda s: "release" if (s.get("officer_outcome") or {}).get("action") == "release" else "terminate",
                            ["release", "terminate"])
    for n in ("release", "terminate", "refuse"):
        g.add_edge(n, "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())
