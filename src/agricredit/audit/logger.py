"""Structured JSONL audit logging.

logs/tool_calls.jsonl   one line per MCP tool call and per LLM call (agent side)
logs/audit_events.jsonl run lifecycle, approvals, release decisions, refusals

Sanitisation: free-text household notes are never logged (only a hash and the
injection flag); large lists are summarised by count and identifiers; values
are truncated. Synthetic data contains no PII, but the logger behaves as if it might.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any

from ..config import get_settings

_LOCK = threading.Lock()
log = logging.getLogger("agricredit")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

REDACT_KEYS = {"notes", "api_key", "authorization", "password"}
MAX_STR = 300


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def sanitize(obj: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "<truncated:depth>"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in REDACT_KEYS:
                out[k] = None if v is None else f"<redacted sha256:{hashlib.sha256(str(v).encode()).hexdigest()[:12]}>"
            elif k == "evidence" and isinstance(v, list):
                out[k] = {"count": len(v), "ids": [e.get("evidence_id") for e in v if isinstance(e, dict)][:40]}
            else:
                out[k] = sanitize(v, depth + 1)
        return out
    if isinstance(obj, list):
        if len(obj) > 12:
            return {"count": len(obj), "first": [sanitize(x, depth + 1) for x in obj[:3]]}
        return [sanitize(x, depth + 1) for x in obj]
    if isinstance(obj, str) and len(obj) > MAX_STR:
        return obj[:MAX_STR] + f"...<+{len(obj) - MAX_STR} chars>"
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat()
    return obj


def source_ids_from(obj: Any) -> list[str]:
    ids: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            if "evidence_id" in o and "source_id" in o:
                ids.append(o["evidence_id"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return sorted(set(ids))[:200]


def _append(name: str, record: dict) -> None:
    d = Path(get_settings().log_dir)
    d.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str, ensure_ascii=False)
    with _LOCK, open(d / name, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_tool_call(*, run_id: str, case_id: str | None, agent_node: str, server: str, tool_name: str,
                  tool_input: dict, output: Any, latency_ms: float, success: bool, model_version: str,
                  error: str | None = None, attempt: int = 1) -> dict:
    rec = {
        "event_id": new_id("tc"), "run_id": run_id, "case_id": case_id, "timestamp": utcnow().isoformat(),
        "agent_node": agent_node, "mcp_server": server, "tool_name": tool_name, "attempt": attempt,
        "sanitized_input": sanitize(tool_input), "output": sanitize(output) if success else None,
        "latency_ms": round(latency_ms, 1), "success": success, "error": error,
        "source_ids": source_ids_from(output) if success else [], "model_version": model_version,
    }
    _append("tool_calls.jsonl", rec)
    return rec


def log_event(*, run_id: str | None, case_id: str | None, event_type: str, details: dict, actor: str = "agent") -> dict:
    rec = {"event_id": new_id("ev"), "run_id": run_id, "case_id": case_id, "timestamp": utcnow().isoformat(),
           "event_type": event_type, "actor": actor, "details": sanitize(details)}
    _append("audit_events.jsonl", rec)
    log.info("audit %s case=%s %s", event_type, case_id, json.dumps(rec["details"], default=str)[:200])
    return rec


def read_jsonl(name: str, *, run_id: str | None = None, case_id: str | None = None, limit: int = 500) -> list[dict]:
    p = Path(get_settings().log_dir) / name
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if run_id and r.get("run_id") != run_id:
            continue
        if case_id and r.get("case_id") != case_id:
            continue
        rows.append(r)
    return rows[-limit:]
