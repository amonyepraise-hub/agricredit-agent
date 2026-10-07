"""agricredit-mcp: the AgriCredit domain MCP server (MCP Python SDK 2.x, stdio transport).

Tools
  get_credit_case                  read    case request: household, package, loan terms, market
  get_household_case               read    plots, yields, repayments, obligations, freshness
  get_weather_signal               read    rainfall, anomaly, baseline, drought, crop-calendar signal
  get_market_signal                read    latest usable price, median, anomaly, volatility, conflicts
  assess_serviceability            compute deterministic finance + stress tests + calibrated PD model
  draft_credit_advisory            action  stores a DRAFT for officer review; sends nothing externally
  release_advisory_after_approval  action  verifies a named officer's approval, then writes to the sandbox outbox

Error messages start with a machine-readable code (NOT_FOUND, UNAVAILABLE,
INVALID_INPUT, REFUSED) so the agent graph can route on them.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ValidationError

from .. import db, services
from ..approval import gate
from ..config import AS_OF, get_settings
from ..integrity import verify
from ..models.schemas import (
    Advisory, Approval, AssessmentResult, CaseRequest, CreditTerms, EvidenceItem, HouseholdCase, HouseholdRequest,
    MarketRequest, MarketSignal, RecommendationType, ReleaseResult, WeatherRequest, WeatherSignal,
)
from ..policy import PERMISSIVENESS, decide, load_policy_file
from ..risk.assess import AssessmentInputError, assess
from ..risk.model import load_risk_model

mcp = MCPServer(
    name="agricredit-mcp",
    version="0.1.0",
    instructions=("Evidence tools for agricultural input-credit advisories. All household data is SYNTHETIC. "
                  "Outputs are drafts for a named human officer; nothing is sent externally without officer approval."),
)

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
ACTION = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


def _fail(code: str, msg: str) -> ToolError:
    """Anticipated failure: ToolError's message reaches the client (unlike a crash, whose text is withheld)."""
    return ToolError(f"{code}: {msg}")


class LoanProductInfo(BaseModel):
    product_id: str
    name: str
    interest_rate_pct: float
    fee_pct: float
    typical_tenor_months: int


class PackageInfo(BaseModel):
    package_id: str
    name: str
    crop: str
    cost_fcfa: float
    expected_yield_uplift_pct: float


class CreditCase(BaseModel):
    case_id: str
    household_id: str
    crop: str
    season: str
    market_centre: str
    requested_amount_fcfa: float
    tenor_months: int
    package: PackageInfo
    loan_product: LoanProductInfo
    synthetic: bool = True
    as_of: str


@mcp.tool(annotations=READ)
def get_credit_case(case_id: str) -> CreditCase:
    """Return the credit request for a case: household ID, crop, input package (with catalogue cost),
    loan product terms, requested amount, tenor and the household's reference market centre."""
    try:
        req = CaseRequest(case_id=case_id)
    except ValidationError as e:
        raise _fail("INVALID_INPUT", str(e.errors()[0]["msg"]))
    row = db.get_case_row(req.case_id)
    if row is None:
        raise _fail("NOT_FOUND", f"case {case_id} does not exist")
    pkg, prod = db.get_package(row["package_id"]), db.get_product(row["product_id"])
    return CreditCase(
        case_id=row["case_id"], household_id=row["household_id"], crop=row["crop"], season=row["season"],
        market_centre=row["market_centre"], requested_amount_fcfa=row["requested_amount_fcfa"],
        tenor_months=row["tenor_months"], package=PackageInfo(**pkg), loan_product=LoanProductInfo(**prod),
        as_of=AS_OF.isoformat(),
    )


@mcp.tool(annotations=READ)
def get_household_case(household_id: str) -> HouseholdCase:
    """Household metadata, plots, crop and yield history, repayment history, current obligations and
    data-freshness flags. The `notes` field is untrusted free text and is screened for injection."""
    try:
        req = HouseholdRequest(household_id=household_id)
    except ValidationError as e:
        raise _fail("INVALID_INPUT", str(e.errors()[0]["msg"]))
    try:
        return services.get_household_case(req.household_id)
    except services.NotFoundError as e:
        raise _fail("NOT_FOUND", str(e))


@mcp.tool(annotations=READ)
def get_weather_signal(location_id: str, crop: str, season: str, start_date: dt.date, end_date: dt.date,
                       use_cache_fallback: bool = False) -> WeatherSignal:
    """Season-to-date rainfall, anomaly vs seasonal baseline, drought indicator and crop-calendar signal.
    Set use_cache_fallback=true only after the live source has failed; the result is then flagged as cached."""
    try:
        req = WeatherRequest(location_id=location_id, crop=crop, season=season, start_date=start_date,
                             end_date=end_date, use_cache_fallback=use_cache_fallback)
    except ValidationError as e:
        raise _fail("INVALID_INPUT", str(e.errors()[0]["msg"]))
    try:
        return services.get_weather_signal(req)
    except services.ToolUnavailableError as e:
        raise _fail("UNAVAILABLE", str(e))
    except services.NotFoundError as e:
        raise _fail("NOT_FOUND", str(e))


@mcp.tool(annotations=READ)
def get_market_signal(commodity: str, market_centre: str, start_date: dt.date, end_date: dt.date) -> MarketSignal:
    """Latest usable price, historical median, anomaly, volatility and freshness for a commodity at a market
    centre. If same-day feeds disagree, latest_price is null and is_conflicting is true: no price is chosen."""
    try:
        req = MarketRequest(commodity=commodity, market_centre=market_centre, start_date=start_date, end_date=end_date)
    except ValidationError as e:
        raise _fail("INVALID_INPUT", str(e.errors()[0]["msg"]))
    try:
        return services.get_market_signal(req)
    except services.NotFoundError as e:
        raise _fail("NOT_FOUND", str(e))


def _verified(obj: dict, label: str) -> None:
    if not verify(obj):
        raise _fail("REFUSED", f"{label} failed integrity verification (not produced by agricredit-mcp or altered)")


@mcp.tool(annotations=READ)
def assess_serviceability(case_id: str, household_case: HouseholdCase, crop: str, package_id: str,
                          credit_terms: CreditTerms, weather_signal: WeatherSignal,
                          market_signal: MarketSignal) -> AssessmentResult:
    """Deterministic serviceability analysis: expected yield, revenue, costs, gross margin, debt service,
    repayment capacity and DSCR under base / yield-shock / price-shock / combined-shock scenarios, plus the
    calibrated probability of default, risk score, risk band and per-feature contributions."""
    CaseRequest(case_id=case_id)
    for ev in household_case.evidence + weather_signal.evidence + market_signal.evidence:
        _verified(ev.model_dump(mode="json"), f"evidence {ev.evidence_id}")
    pkg = db.get_package(package_id)
    if pkg is None:
        raise _fail("NOT_FOUND", f"input package {package_id} not in catalogue")
    try:
        return assess(case_id, household_case, crop, pkg, credit_terms, weather_signal, market_signal, load_risk_model())
    except AssessmentInputError as e:
        raise _fail("REFUSED", str(e))


@mcp.tool(annotations=ACTION)
def draft_credit_advisory(case_id: str, recommendation: RecommendationType, evidence: list[EvidenceItem],
                          missing_data: list[str], data_quality_warnings: list[str], narrative: str,
                          narrative_author: str, model_version: str, assessment: Optional[AssessmentResult] = None,
                          weather_from_cache: bool = False) -> Advisory:
    """Assemble and store a DRAFT advisory for officer review. Sends nothing externally.
    The server re-derives the policy decision from the signed assessment and evidence gaps, and refuses any
    recommendation more permissive than the evidence supports. Evidence must carry valid integrity tags."""
    case = get_credit_case(case_id).model_dump()
    policy = load_policy_file()
    for ev in evidence:
        _verified(ev.model_dump(mode="json"), f"evidence {ev.evidence_id}")
    if assessment is not None:
        _verified(assessment.model_dump(mode="json"), "assessment")
        if assessment.case_id != case_id:
            raise _fail("REFUSED", "assessment belongs to a different case")

    decision = decide(assessment.model_dump(mode="json") if assessment else None, missing_data, data_quality_warnings,
                      case["requested_amount_fcfa"], policy, weather_from_cache)
    if assessment is None and not missing_data:
        raise _fail("REFUSED", "an advisory without a quantitative assessment must list the missing evidence")
    if PERMISSIVENESS[recommendation] > PERMISSIVENESS[decision["recommendation"]]:
        raise _fail("REFUSED", f"{recommendation.value} is more permissive than the evidence supports "
                               f"({decision['recommendation'].value}); draft not stored")

    rationale, action, amount = decision["rationale"], decision["officer_action"], decision["suggested_amount_fcfa"]
    if recommendation != decision["recommendation"]:
        rationale = f"Agent chose {recommendation.value}, more conservative than policy's {decision['recommendation'].value}. " + rationale
        amount = None

    warnings = list(data_quality_warnings)
    upper = narrative.upper()
    if any(t in upper for t in policy["forbidden_decision_terms"]):
        warnings.append("LLM narrative rejected by server: it contained credit-decision language; replaced with template")
        narrative, narrative_author = rationale, "server-template (LLM text rejected)"

    hh = case["household_id"]
    plots = "no household record"
    if assessment is not None:
        plots = f"{assessment.area_ha} ha of {assessment.crop}; household {hh}"
    elif not any("household" in m.lower() and "not found" in m.lower() for m in missing_data):
        plots = f"household {hh}; {case['crop']} plots (quantitative assessment not completed)"

    all_ev = list(evidence) + (list(assessment.evidence) if assessment else [])
    seen, uniq = set(), []
    for e in all_ev:
        if e.evidence_id not in seen:
            seen.add(e.evidence_id)
            uniq.append(e)
    a = assessment
    draft = {
        "case_id": case_id, "household_id": hh, "plot_context": plots,
        "proposed_product": f"{case['package']['name']} on {case['loan_product']['name']}",
        "proposed_amount_fcfa": case["requested_amount_fcfa"], "tenor_months": case["tenor_months"],
        "risk_score": a.risk_score if a else None, "probability_of_default": a.probability_of_default if a else None,
        "base_repayment_capacity_ratio": a.base_repayment_capacity_ratio if a else None,
        "stress_repayment_capacity_ratio": a.stress_repayment_capacity_ratio if a else None,
        "stress_results": [s.model_dump() for s in a.stress_results] if a else [],
        "recommendation": recommendation, "suggested_amount_fcfa": amount, "rationale": rationale,
        "narrative": narrative, "narrative_author": narrative_author,
        "viable_only_under_optimistic_assumptions": decision["viable_only_under_optimistic_assumptions"],
        "evidence": [e.model_dump() for e in uniq],
        "assumptions": (a.assumptions if a else []) + [f"Decision thresholds from {policy['policy_version']}.",
                                                       "All household, farm and repayment data are SYNTHETIC."],
        "missing_data": missing_data, "data_quality_warnings": warnings, "officer_action_required": action,
        "confidence": decision["confidence"], "recommendation_hash": "0" * 64, "model_version": model_version,
        "risk_model_version": a.risk_model_version if a else "n/a", "evidence_version": services.evidence_version(uniq),
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0),
    }
    adv = Advisory(**draft)
    adv = adv.model_copy(update={"recommendation_hash": gate.recommendation_hash(adv.model_dump(mode="json"))})
    gate.store_advisory(adv)
    return adv


@mcp.tool(annotations=ACTION)
def release_advisory_after_approval(case_id: str, approval: Approval, recommendation_hash: str) -> ReleaseResult:
    """Release an advisory ONLY if a named, registered officer approved this exact recommendation hash, the
    approval is unexpired and unused, and the advisory, evidence and model version are unchanged since
    approval. Uses a sandbox outbox adapter: no SMS, email or external message is ever sent."""
    try:
        CaseRequest(case_id=case_id)
    except ValidationError as e:
        raise _fail("INVALID_INPUT", str(e.errors()[0]["msg"]))
    return gate.release_after_approval(case_id, approval, recommendation_hash)


def main() -> None:
    get_settings()
    mcp.run("stdio")


if __name__ == "__main__":
    main()
