"""Human approval gate and sandbox release adapter.

Approvals are recorded by a named officer through the dashboard or CLI. There is
deliberately NO MCP tool that creates an approval: the agent can request a
release, but it cannot approve its own advisory.

release_after_approval() re-derives everything from the database rather than
trusting the caller: the approval must exist exactly as stored, belong to a
registered active officer, be unexpired, unconsumed, for this case, and bound
to the hash, evidence version and model version of the case's CURRENT advisory.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

from .. import db
from ..audit.logger import log_event, new_id, utcnow
from ..config import get_settings
from ..models.schemas import Advisory, Approval, ApprovalDecision, ReleaseResult

HASHED_FIELDS = [
    "case_id", "household_id", "proposed_product", "proposed_amount_fcfa", "tenor_months", "risk_score",
    "probability_of_default", "base_repayment_capacity_ratio", "stress_repayment_capacity_ratio", "recommendation",
    "suggested_amount_fcfa", "rationale", "narrative", "viable_only_under_optimistic_assumptions", "missing_data",
    "data_quality_warnings", "officer_action_required", "model_version", "risk_model_version", "evidence_version",
]


class ApprovalError(ValueError):
    pass


def recommendation_hash(advisory: dict) -> str:
    canon = {k: advisory.get(k) for k in HASHED_FIELDS}
    canon["stress_results"] = advisory.get("stress_results")
    canon["evidence_ids"] = sorted(e["evidence_id"] for e in advisory.get("evidence", []))
    return hashlib.sha256(json.dumps(canon, sort_keys=True, default=str).encode()).hexdigest()


def store_advisory(advisory: Advisory) -> None:
    with db.write_conn() as c:
        c.execute("INSERT OR REPLACE INTO advisories VALUES (?,?,?,?)",
                  (advisory.case_id, advisory.recommendation_hash, advisory.model_dump_json(), utcnow().isoformat()))


def current_advisory(case_id: str) -> Advisory | None:
    with db.read_conn() as c:
        r = c.execute("SELECT advisory_json FROM advisories WHERE case_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                      (case_id,)).fetchone()
    return Advisory.model_validate_json(r["advisory_json"]) if r else None


def record_approval(*, case_id: str, recommendation_hash_: str, officer_id: str, officer_name: str,
                    decision: ApprovalDecision, reason: str | None = None) -> Approval:
    """Called by the HUMAN officer's interface only."""
    officer = db.get_officer(officer_id)
    if officer is None:
        raise ApprovalError(f"officer {officer_id} is not a registered active officer")
    if officer["officer_name"] != officer_name:
        raise ApprovalError("officer name does not match the officer register")
    adv = current_advisory(case_id)
    if adv is None or adv.recommendation_hash != recommendation_hash_:
        raise ApprovalError("no current advisory with that recommendation hash for this case")
    if decision == ApprovalDecision.REJECT and not reason:
        raise ApprovalError("a reason is required when rejecting an advisory")
    appr = Approval(approval_id=new_id("appr"), case_id=case_id, recommendation_hash=recommendation_hash_,
                    officer_id=officer_id, officer_name=officer_name, decision=decision, timestamp=utcnow(),
                    reason=reason, model_version=adv.model_version, evidence_version=adv.evidence_version)
    with db.write_conn() as c:
        c.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                  (appr.approval_id, appr.case_id, appr.recommendation_hash, appr.officer_id, appr.officer_name,
                   appr.decision.value, appr.timestamp.isoformat(), appr.reason, appr.model_version, appr.evidence_version))
    log_event(run_id=None, case_id=case_id, event_type=f"officer_{decision.value.lower()}", actor=officer_id,
              details={"approval_id": appr.approval_id, "recommendation_hash": recommendation_hash_,
                       "officer_name": officer_name, "reason": reason, "recommendation": adv.recommendation.value})
    return appr


def get_approval(approval_id: str) -> Approval | None:
    with db.read_conn() as c:
        r = c.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d.pop("consumed")
    return Approval(**d)


def _is_consumed(approval_id: str) -> bool:
    with db.read_conn() as c:
        r = c.execute("SELECT consumed FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        o = c.execute("SELECT 1 FROM release_outbox WHERE approval_id = ?", (approval_id,)).fetchone()
    return bool((r and r["consumed"]) or o)


def release_after_approval(case_id: str, approval: Approval, recommendation_hash_: str, run_id: str | None = None) -> ReleaseResult:
    s = get_settings()
    stored = get_approval(approval.approval_id)
    officer = db.get_officer(approval.officer_id)
    adv = current_advisory(case_id)
    now = utcnow()
    ts = approval.timestamp if approval.timestamp.tzinfo else approval.timestamp.replace(tzinfo=dt.timezone.utc)

    checks = {
        "approval_exists": stored is not None,
        "approval_matches_stored_record": stored is not None and stored.model_dump() == approval.model_dump(),
        "decision_is_approve": approval.decision == ApprovalDecision.APPROVE,
        "case_matches": approval.case_id == case_id,
        "named_officer_registered": officer is not None and officer["officer_name"] == approval.officer_name,
        "approval_not_expired": dt.timedelta(0) <= now - ts <= dt.timedelta(hours=s.approval_ttl_hours),
        "approval_not_replayed": stored is not None and not _is_consumed(approval.approval_id),
        "hash_matches_approval": approval.recommendation_hash == recommendation_hash_,
        "advisory_exists": adv is not None,
        "recommendation_unchanged": adv is not None and adv.recommendation_hash == approval.recommendation_hash
                                     and recommendation_hash(adv.model_dump(mode="json")) == adv.recommendation_hash,
        "evidence_unchanged": adv is not None and adv.evidence_version == approval.evidence_version,
        "model_version_unchanged": (not s.model_version_approval_sensitive)
                                   or (adv is not None and adv.model_version == approval.model_version),
    }
    failed = [k for k, v in checks.items() if not v]
    if failed:
        reason = "release blocked: " + ", ".join(failed)
        log_event(run_id=run_id, case_id=case_id, event_type="release_blocked",
                  details={"failed_checks": failed, "approval_id": approval.approval_id, "presented_hash": recommendation_hash_})
        return ReleaseResult(released=False, case_id=case_id, reason=reason, checks=checks)

    release_id = new_id("rel")
    payload = {"release_id": release_id, "case_id": case_id, "recommendation": adv.recommendation.value,
               "recommendation_hash": adv.recommendation_hash, "approved_by": approval.officer_id,
               "channel": "sandbox-outbox", "note": "Sandbox adapter: nothing is sent to any farmer or external system."}
    with db.write_conn() as c:
        c.execute("INSERT INTO release_outbox VALUES (?,?,?,?,?)",
                  (release_id, case_id, approval.approval_id, json.dumps(payload), now.isoformat()))
        c.execute("UPDATE approvals SET consumed = 1 WHERE approval_id = ?", (approval.approval_id,))
    log_event(run_id=run_id, case_id=case_id, event_type="advisory_released",
              details={"release_id": release_id, "approval_id": approval.approval_id, "officer_id": approval.officer_id,
                       "recommendation": adv.recommendation.value, "recommendation_hash": adv.recommendation_hash})
    return ReleaseResult(released=True, case_id=case_id, reason="all approval checks passed; written to sandbox outbox",
                         checks=checks, release_id=release_id, released_at=now)
