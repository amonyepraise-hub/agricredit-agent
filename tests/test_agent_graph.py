"""The LangGraph agent end-to-end over stdio MCP (own server + official filesystem server), offline stub LLM."""
import pytest

from agricredit.agent.runner import AgentRunner


@pytest.fixture(scope="module")
def runner():
    return AgentRunner()


def test_strong_case_pauses_for_officer(runner):
    st = runner.start("BEN-0001")
    assert st["status"] == "AWAITING_APPROVAL" and st["_waiting_for_officer"]
    assert st["advisory"]["recommendation"] == "RECOMMEND"
    nodes = [a["node"] for a in st["activity"]]
    for n in ["load_case", "validate_data", "plan_investigation", "get_household_evidence", "get_weather_evidence",
              "get_market_evidence", "quantitative_assessment", "contradiction_check", "evidence_sufficiency", "draft_advisory"]:
        assert n in nodes


def test_officer_termination(runner):
    st = runner.start("BEN-0001")
    st = runner.resume(st["run_id"], {"action": "terminate", "reason": "household withdrew"})
    assert st["status"] == "TERMINATED_BY_OFFICER" and not st.get("release")


def test_unknown_household_refused_without_draft(runner):
    st = runner.start("BEN-0009")
    assert st["status"] == "REFUSED" and not st.get("advisory")


def test_conflicting_market_requests_evidence(runner):
    st = runner.start("BEN-0005")
    assert st["advisory"]["recommendation"] == "REQUEST_MORE_EVIDENCE"
    assert st["advisory"]["risk_score"] is None


def test_release_without_approval_is_blocked(runner):
    st = runner.start("BEN-0001")
    st = runner.resume(st["run_id"], {"action": "release", "approval": None})
    assert st["status"] == "RELEASE_BLOCKED"
