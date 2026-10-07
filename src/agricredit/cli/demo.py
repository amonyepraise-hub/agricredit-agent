"""Scripted terminal demo (under three minutes). Every line printed is produced by the running system.

Shows: case selection, agent planning, MCP tool calls, weather and market evidence, risk calculation, stress
test, recommendation, a deliberate data problem (rainfall-service outage) with recovery and refusal, officer
approval, gated release, a blocked unauthorized release, and the audit log.
"""
from __future__ import annotations

import os
import time

from ..approval import gate
from ..audit.logger import read_jsonl
from ..config import get_settings
from ..db import get_officer
from ..models.schemas import ApprovalDecision
from .main import show

W = 100


def banner(n: int, text: str) -> None:
    print("\n" + "#" * W + f"\n# STEP {n}: {text}\n" + "#" * W, flush=True)


def demo(runner) -> int:
    s = get_settings()
    t0 = time.perf_counter()
    print(f"AgriCredit Agent demo | LLM backend: {s.model_version} | synthetic data only")
    if not s.is_open_weight_backend:
        print("NOTE: running with the offline stub (no language model). Set AGRICREDIT_LLM_BACKEND=ollama for Qwen.")

    banner(1, "Officer selects case BEN-0421 (Maize Input Package B, 185,000 FCFA). Agent plans, calls MCP tools, "
              "computes risk and stress tests, drafts an advisory, then STOPS for a human.")
    st = runner.start("BEN-0421")
    show(st)

    banner(2, "Deliberate data problem: the rainfall service for zone BEN-LOC-03 goes down while assessing BEN-0008")
    os.environ["AGRICREDIT_FAULTS"] = "weather_unavailable:BEN-LOC-03"
    try:
        bad = runner.start("BEN-0008")
    finally:
        os.environ["AGRICREDIT_FAULTS"] = ""
    show(bad)
    retries = [c for c in read_jsonl("tool_calls.jsonl", run_id=bad["run_id"]) if c["tool_name"] == "get_weather_signal"]
    for c in retries:
        print(f"   logged: get_weather_signal attempt {c['attempt']} success={c['success']} "
              f"cache_fallback={c['sanitized_input'].get('use_cache_fallback', False)} {(c['error'] or '')[:70]}")

    banner(3, "An unauthorized release attempt on BEN-0421 (no officer approval exists)")
    fake = {"approval_id": "appr-000000000000", "case_id": "BEN-0421", "recommendation_hash": st["advisory"]["recommendation_hash"],
            "officer_id": "OFF-001", "officer_name": get_officer("OFF-001")["officer_name"], "decision": "APPROVE",
            "timestamp": "2026-10-07T09:00:00Z", "reason": None, "model_version": st["advisory"]["model_version"],
            "evidence_version": st["advisory"]["evidence_version"]}
    st_fake = runner.start("BEN-0421")
    blocked = runner.resume(st_fake["run_id"], {"action": "release", "approval": fake})
    print(f"   release result: released={blocked['release']['released']} | {blocked['release']['reason']}")

    banner(4, "Named officer OFF-001 reviews and APPROVES the BEN-0421 draft; the release gate verifies and releases to the sandbox outbox")
    officer = get_officer("OFF-001")
    st = runner.start("BEN-0421")
    appr = gate.record_approval(case_id="BEN-0421", recommendation_hash_=st["advisory"]["recommendation_hash"],
                                officer_id=officer["officer_id"], officer_name=officer["officer_name"],
                                decision=ApprovalDecision.APPROVE, reason="Reviewed; household to confirm repayment status")
    print(f"   approval recorded: {appr.approval_id} by {appr.officer_name} for hash {appr.recommendation_hash[:16]}...")
    seen = len(st["activity"])
    done = runner.resume(st["run_id"], {"action": "release", "approval": appr.model_dump(mode="json"),
                                        "recommendation_hash": st["advisory"]["recommendation_hash"]})
    show(done, skip=seen)

    banner(5, "Audit trail for the approved run (logs/tool_calls.jsonl, logs/audit_events.jsonl)")
    for c in read_jsonl("tool_calls.jsonl", run_id=done["run_id"]):
        print(f"   {c['timestamp'][11:19]} {c['agent_node']:<24} {c['mcp_server']:<10} {c['tool_name']:<34} "
              f"ok={c['success']!s:<5} {c['latency_ms']:>7.1f} ms  sources={len(c['source_ids'])}")
    for e in read_jsonl("audit_events.jsonl", case_id="BEN-0421")[-4:]:
        print(f"   EVENT {e['timestamp'][11:19]} {e['event_type']:<28} actor={e['actor']}")
    print(f"\nDemo finished in {time.perf_counter() - t0:.0f} s.")
    return 0
