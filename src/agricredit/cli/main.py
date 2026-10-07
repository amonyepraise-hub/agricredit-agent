"""Command-line interface.

  python -m agricredit.cli run BEN-0421                       run a case to the approval gate and print the trace
  python -m agricredit.cli run BEN-0421 --approve OFF-001     ...then record the named officer's approval and release
  python -m agricredit.cli run BEN-0008 --fault weather_unavailable:BEN-LOC-03
  python -m agricredit.cli demo                               scripted end-to-end demo (see README)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from ..approval import gate
from ..config import get_settings
from ..models.schemas import ApprovalDecision

W = 100


def _p(s: str = "") -> None:
    print(s, flush=True)


def show(state: dict, verbose: bool = False, skip: int = 0) -> None:
    for a in state.get("activity", [])[skip:]:
        tool = f"  <{a['tool']}>" if a.get("tool") else ""
        _p(f"  [{a['node']}] {a['message']}{tool}")
    adv = state.get("advisory")
    _p("-" * W)
    _p(f"STATUS: {state.get('status')}")
    if state.get("refusal_reason"):
        _p(f"REFUSAL: {state['refusal_reason']}")
    if adv:
        _p(f"RECOMMENDATION: {adv['recommendation']}"
           + (f"  (suggested amount {adv['suggested_amount_fcfa']:,.0f} FCFA)" if adv.get("suggested_amount_fcfa") else ""))
        if adv.get("risk_score") is not None:
            _p(f"Risk score {adv['risk_score']}/100 | PD {adv['probability_of_default']:.3f} | capacity "
               f"{adv['base_repayment_capacity_ratio']:.2f}x base, {adv['stress_repayment_capacity_ratio']:.2f}x combined shock")
        for s in adv["stress_results"]:
            _p(f"   {s['scenario']:<15} capacity {s['repayment_capacity_ratio']:>6.2f}x   revenue {s['expected_revenue_fcfa']:>10,.0f} FCFA")
        if adv["viable_only_under_optimistic_assumptions"]:
            _p("   ! Viable only under optimistic assumptions")
        for m in adv["missing_data"]:
            _p(f"   MISSING: {m}")
        for w in adv["data_quality_warnings"]:
            _p(f"   WARNING: {w}")
        _p(f"Officer action: {adv['officer_action_required']}")
        _p(f"Narrative ({adv['narrative_author']}): {adv['narrative'][:600]}")
        _p(f"Evidence items cited: {len(adv['evidence'])} | hash {adv['recommendation_hash'][:16]}...")
    if state.get("release"):
        _p(f"RELEASE: {json.dumps(state['release'], default=str)[:400]}")
    _p("=" * W)


def run_case(runner, case_id: str, approve: str | None, reject: str | None, verbose: bool) -> dict:
    from ..db import get_officer
    _p("=" * W)
    _p(f"Case {case_id}  |  model backend: {get_settings().model_version}  |  faults: {get_settings().faults or 'none'}")
    _p("=" * W)
    st = runner.start(case_id)
    show(st, verbose)
    if st.get("_waiting_for_officer") and (approve or reject):
        officer = get_officer(approve or reject)
        if officer is None:
            _p(f"Officer {approve or reject} not registered")
            return st
        adv = st["advisory"]
        decision = ApprovalDecision.APPROVE if approve else ApprovalDecision.REJECT
        appr = gate.record_approval(case_id=case_id, recommendation_hash_=adv["recommendation_hash"],
                                    officer_id=officer["officer_id"], officer_name=officer["officer_name"],
                                    decision=decision, reason=None if approve else "Rejected via CLI")
        _p(f"Officer {officer['officer_name']} recorded {decision.value} ({appr.approval_id})")
        payload = ({"action": "release", "approval": appr.model_dump(mode="json"),
                    "recommendation_hash": adv["recommendation_hash"]} if approve
                   else {"action": "terminate", "reason": "Rejected by officer"})
        seen = len(st.get("activity", []))
        st = runner.resume(st["run_id"], payload)
        show(st, verbose, skip=seen)
    return st


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agricredit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("case_id")
    r.add_argument("--approve", metavar="OFFICER_ID")
    r.add_argument("--reject", metavar="OFFICER_ID")
    r.add_argument("--fault", action="append", default=[])
    r.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("demo")
    sub.add_parser("cases")
    a = ap.parse_args(argv)

    if getattr(a, "fault", None):
        os.environ["AGRICREDIT_FAULTS"] = ",".join(a.fault)
    from ..agent.runner import AgentRunner
    runner = AgentRunner()

    if a.cmd == "cases":
        from ..db import list_cases
        for c in list_cases():
            _p(f"{c['case_id']}  {c['household_id']}  {c['crop']:<8} {c['requested_amount_fcfa']:>9,.0f} FCFA  {c['scenario_tag']}")
        return 0
    if a.cmd == "run":
        run_case(runner, a.case_id, a.approve, a.reject, a.verbose)
        return 0
    from .demo import demo
    return demo(runner)


if __name__ == "__main__":
    sys.exit(main())
