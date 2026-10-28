"""Pydantic schemas shared by the MCP server, risk model, agent graph and UI.

Every MCP tool input and output is validated against these models with
`extra="forbid"`. Nothing crosses a tool boundary without strict validation:
this is the first line of defence against malformed IDs, path traversal via
identifier fields, and injected fields riding in on data.
"""
from __future__ import annotations

import datetime as dt
import re
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

CASE_ID_RE = re.compile(r"^BEN-[0-9]{4}$")
HOUSEHOLD_ID_RE = re.compile(r"^HH-[0-9]{5}$")
LOCATION_ID_RE = re.compile(r"^BEN-LOC-[0-9]{2}$")
OFFICER_ID_RE = re.compile(r"^OFF-[0-9]{3}$")
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

Crop = Literal["maize", "cotton", "rice", "cashew", "soybean"]


def _match(regex: re.Pattern, value: str, label: str) -> str:
    if not isinstance(value, str) or not regex.fullmatch(value):
        raise ValueError(f"malformed {label}: {value!r}")
    return value


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------

class EvidenceKind(str, Enum):
    OBSERVED = "observed"
    DERIVED = "derived"
    MODEL_PREDICTION = "model_prediction"
    AGENT_CONCLUSION = "agent_conclusion"


class EvidenceItem(Strict):
    evidence_id: str
    label: str
    source_type: str
    source_id: str
    source_uri: str
    observation_timestamp: Optional[dt.datetime] = None
    retrieval_timestamp: dt.datetime
    value: str
    kind: EvidenceKind
    transformation: Optional[str] = None
    is_stale: bool = False
    staleness_days: Optional[float] = None
    synthetic: bool = True
    integrity: Optional[str] = None  # HMAC set by agricredit-mcp; verified before an item can enter an advisory


# --------------------------------------------------------------------------
# Tool inputs
# --------------------------------------------------------------------------

class CaseRequest(Strict):
    case_id: str

    @field_validator("case_id")
    @classmethod
    def _v(cls, v: str) -> str:
        return _match(CASE_ID_RE, v, "case_id")


class HouseholdRequest(Strict):
    household_id: str

    @field_validator("household_id")
    @classmethod
    def _v(cls, v: str) -> str:
        return _match(HOUSEHOLD_ID_RE, v, "household_id")


class WeatherRequest(Strict):
    location_id: str
    crop: Crop
    season: str = Field(pattern=r"^20[0-9]{2}$")
    start_date: dt.date
    end_date: dt.date
    use_cache_fallback: bool = False

    @field_validator("location_id")
    @classmethod
    def _v(cls, v: str) -> str:
        return _match(LOCATION_ID_RE, v, "location_id")


class MarketRequest(Strict):
    commodity: Crop
    market_centre: str
    start_date: dt.date
    end_date: dt.date

    @field_validator("market_centre")
    @classmethod
    def _v(cls, v: str) -> str:
        return _match(SAFE_TOKEN_RE, v, "market_centre")


class CreditTerms(Strict):
    package_id: str
    product_id: str
    proposed_amount_fcfa: float = Field(gt=0, le=5_000_000)
    tenor_months: int = Field(gt=0, le=24)
    interest_rate_pct: float = Field(ge=0, le=60)
    fee_pct: float = Field(ge=0, le=20)

    @field_validator("package_id", "product_id")
    @classmethod
    def _v(cls, v: str) -> str:
        return _match(SAFE_TOKEN_RE, v, "identifier")


# --------------------------------------------------------------------------
# Domain records
# --------------------------------------------------------------------------

class Plot(Strict):
    plot_id: str
    crop: str
    area_ha: float
    soil_type: Optional[str] = None
    location_id: str


class YieldRecord(Strict):
    record_id: int
    season: str
    crop: str
    plot_id: str
    yield_kg_per_ha: float
    recorded_at: dt.datetime


class RepaymentRecord(Strict):
    loan_id: str
    product: str
    amount_fcfa: float
    due_date: dt.date
    paid_date: Optional[dt.date] = None
    days_overdue: int = 0
    defaulted: bool = False
    recorded_at: dt.datetime


class CurrentObligation(Strict):
    obligation_id: int
    product: str
    outstanding_amount_fcfa: float
    monthly_due_fcfa: float


class DataFreshness(Strict):
    repayment_record_age_days: Optional[float] = None
    repayment_is_stale: bool = False
    repayment_records_missing: bool = False
    yield_seasons_observed: int = 0


class HouseholdCase(Strict):
    household_id: str
    synthetic: bool = True
    location_id: str
    plots: list[Plot]
    yield_history: list[YieldRecord]
    repayment_history: list[RepaymentRecord]
    current_obligations: list[CurrentObligation]
    data_freshness: DataFreshness
    # Free text from the household record. UNTRUSTED. Never executed, never
    # placed in an instruction position of any prompt; only shown inside a
    # delimited data block and screened for injection patterns.
    notes: Optional[str] = None
    notes_flagged_as_injection: bool = False
    evidence: list[EvidenceItem]


class WeatherSignal(Strict):
    location_id: str
    crop: str
    season: str
    rainfall_mm: float
    rainfall_anomaly_pct: float
    seasonal_baseline_mm: float
    drought_indicator: bool
    crop_calendar_signal: str
    observation_timestamp: dt.datetime
    retrieval_timestamp: dt.datetime
    source_dataset: str
    age_days: float
    is_stale: bool
    from_cache_fallback: bool = False
    evidence: list[EvidenceItem]


class MarketSignal(Strict):
    commodity: str
    market_centre: str
    latest_price_fcfa_per_kg: Optional[float]
    historical_median_fcfa_per_kg: float
    price_anomaly_pct: Optional[float]
    volatility: float
    observation_timestamp: Optional[dt.datetime]
    retrieval_timestamp: dt.datetime
    source_dataset: str
    age_days: Optional[float]
    is_stale: bool
    is_conflicting: bool
    conflicting_observations: list[dict] = Field(default_factory=list)
    n_observations: int
    # Seasonality (real-open-data mode; None when there is no multi-year history, e.g. the synthetic weekly feed)
    seasonal_median_fcfa_per_kg: Optional[float] = None   # median price in the same calendar month of earlier years
    seasonal_anomaly_pct: Optional[float] = None
    seasonal_years: Optional[str] = None
    monthly_median_fcfa_per_kg: dict[str, float] = Field(default_factory=dict)  # "1".."12" -> median of earlier years
    evidence: list[EvidenceItem]


class RiskBand(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"


class StressCaseResult(Strict):
    scenario: Literal["base_case", "yield_shock", "price_shock", "combined_shock"]
    yield_kg_per_ha: float
    price_fcfa_per_kg: float
    expected_revenue_fcfa: float
    expected_gross_margin_fcfa: float
    debt_service_fcfa: float
    repayment_capacity_ratio: float
    dscr: float


class AssessmentResult(Strict):
    case_id: str
    crop: str
    area_ha: float
    expected_yield_kg_per_ha: float
    expected_revenue_fcfa: float
    expected_cost_fcfa: float
    expected_gross_margin_fcfa: float
    debt_service_fcfa: float
    base_repayment_capacity_ratio: float
    stress_repayment_capacity_ratio: float
    stress_results: list[StressCaseResult]
    max_serviceable_amount_fcfa: float
    probability_of_default: float = Field(ge=0, le=1)
    risk_score: int = Field(ge=0, le=100)
    risk_band: RiskBand
    feature_values: dict[str, float]
    feature_contributions: dict[str, float]
    assumptions: list[str]
    risk_model_version: str
    assessed_at: dt.datetime
    # Repayment timing (real-open-data mode): capacity if repayment is moved to the month when prices usually recover
    repayment_timing: Optional[dict] = None
    # Crop price at which repayment capacity reaches 1.0x, in the base case and under the yield shock (+ price shock)
    break_even_price_fcfa_per_kg: Optional[float] = None
    stress_break_even_price_fcfa_per_kg: Optional[float] = None
    evidence: list[EvidenceItem]
    integrity: Optional[str] = None


class RecommendationType(str, Enum):
    RECOMMEND = "RECOMMEND"
    RECOMMEND_WITH_CONDITIONS = "RECOMMEND_WITH_CONDITIONS"
    REQUEST_MORE_EVIDENCE = "REQUEST_MORE_EVIDENCE"
    REDUCE_CREDIT_AMOUNT = "REDUCE_CREDIT_AMOUNT"
    NO_RECOMMENDATION = "NO_RECOMMENDATION"


class Advisory(Strict):
    case_id: str
    household_id: str
    synthetic_data: bool = True
    plot_context: str
    proposed_product: str
    proposed_amount_fcfa: float
    tenor_months: int
    risk_score: Optional[int]
    probability_of_default: Optional[float]
    base_repayment_capacity_ratio: Optional[float]
    stress_repayment_capacity_ratio: Optional[float]
    stress_results: list[StressCaseResult]
    recommendation: RecommendationType
    suggested_amount_fcfa: Optional[float] = None
    rationale: str
    narrative: str
    narrative_author: str
    viable_only_under_optimistic_assumptions: bool
    evidence: list[EvidenceItem]
    assumptions: list[str]
    missing_data: list[str]
    data_quality_warnings: list[str]
    officer_action_required: str
    confidence: float = Field(ge=0, le=1)
    recommendation_hash: str
    model_version: str
    risk_model_version: str
    evidence_version: str
    generated_at: dt.datetime
    status: Literal["DRAFT_PENDING_OFFICER_REVIEW"] = "DRAFT_PENDING_OFFICER_REVIEW"


# --------------------------------------------------------------------------
# Approval / release
# --------------------------------------------------------------------------

class ApprovalDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class Approval(Strict):
    approval_id: str
    case_id: str
    recommendation_hash: str
    officer_id: str
    officer_name: str
    decision: ApprovalDecision
    timestamp: dt.datetime
    reason: Optional[str] = None
    model_version: str
    evidence_version: str

    @field_validator("case_id")
    @classmethod
    def _c(cls, v: str) -> str:
        return _match(CASE_ID_RE, v, "case_id")

    @field_validator("officer_id")
    @classmethod
    def _o(cls, v: str) -> str:
        return _match(OFFICER_ID_RE, v, "officer_id")

    @field_validator("recommendation_hash")
    @classmethod
    def _h(cls, v: str) -> str:
        return _match(SHA256_RE, v, "recommendation_hash")


class ReleaseResult(Strict):
    released: bool
    case_id: str
    reason: str
    checks: dict[str, bool]
    release_id: Optional[str] = None
    released_at: Optional[dt.datetime] = None
    adapter: str = "sandbox-outbox (no external message is sent)"
