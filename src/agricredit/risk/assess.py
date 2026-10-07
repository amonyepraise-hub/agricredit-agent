"""Serviceability assessment: deterministic finance + statistical PD model.
Shared by the MCP tool (inference) and risk/train.py (training), so features
are computed identically in both."""
from __future__ import annotations

import math

import numpy as np

from ..config import AS_OF, get_settings
from ..models.schemas import (
    AssessmentResult, CreditTerms, EvidenceItem, EvidenceKind, HouseholdCase, MarketSignal, RiskBand,
    StressCaseResult, WeatherSignal,
)
from ..integrity import signed
from . import assumptions as A
from . import financial as fin

FEATURES = [
    "repayment_rate", "late_rate", "default_count", "max_days_overdue", "n_loans",
    "yield_ratio", "yield_cv", "yield_trend", "area_ha", "n_seasons",
    "price_anomaly_pct", "price_volatility", "rainfall_anomaly_pct", "drought",
    "log_amount", "obligations_to_debt_service", "base_capacity", "combined_capacity",
]
# Data-quality signals (record freshness, missing features, conflicting records) are deliberately NOT model
# features: they carry no outcome signal in the synthetic training data, so a model would learn noise and
# extrapolate on unseen values. They are enforced by the policy gate instead (warnings and blocking gaps).
DATA_QUALITY_SIGNALS = ["repayment_age_days", "missing_feature_count", "conflicting_record_count"]


class AssessmentInputError(ValueError):
    pass


def financial_inputs(case: HouseholdCase, crop: str, package: dict, terms: CreditTerms,
                     weather: WeatherSignal, price: float, fraction: float = 1.0) -> tuple[fin.FinancialInputs, float, float]:
    """`fraction` scales the package (cost and agronomic uplift) and the loan together, used to size reduced offers."""
    plots = [p for p in case.plots if p.crop == crop]
    if not plots:
        raise AssessmentInputError(f"household has no {crop} plot")
    area = round(sum(p.area_ha for p in plots), 2)
    ys = [y.yield_kg_per_ha for y in case.yield_history if y.crop == crop]
    if not ys:
        raise AssessmentInputError(f"no {crop} yield history")
    hist_mean = float(np.mean(ys))
    deficit = max(0.0, -weather.rainfall_anomaly_pct - A.RAINFALL_DEFICIT_TOLERANCE_PCT)
    weather_factor = max(0.3, 1 - A.RAINFALL_DEFICIT_YIELD_ELASTICITY * deficit / 100)
    uplift = 1 + package["expected_yield_uplift_pct"] * A.PACKAGE_UPLIFT_REALISATION * fraction
    exp_yield = round(hist_mean * uplift * weather_factor, 2)
    monthly_oblig = sum(o.monthly_due_fcfa for o in case.current_obligations)
    inp = fin.FinancialInputs(
        expected_yield_kg_per_ha=exp_yield, area_ha=area, price_fcfa_per_kg=price,
        marketed_share=A.MARKETED_SHARE[crop], input_cost_fcfa=package["cost_fcfa"] * fraction,
        other_production_cost_fcfa=A.OTHER_PRODUCTION_COST_FCFA_PER_HA[crop] * area,
        proposed_amount_fcfa=terms.proposed_amount_fcfa * fraction, existing_monthly_obligations_fcfa=monthly_oblig,
        tenor_months=terms.tenor_months, interest_rate_pct=terms.interest_rate_pct, fee_pct=terms.fee_pct,
    )
    return inp, hist_mean, weather_factor


def build_features(case: HouseholdCase, crop: str, inp: fin.FinancialInputs, stress: list[dict],
                   weather: WeatherSignal, market: MarketSignal) -> dict[str, float]:
    rep = case.repayment_history
    n = len(rep)
    on_time = sum(1 for r in rep if not r.defaulted and r.days_overdue <= 7)
    late = sum(1 for r in rep if r.days_overdue > 7)
    ys_by_season: dict[str, list[float]] = {}
    for y in case.yield_history:
        if y.crop == crop:
            ys_by_season.setdefault(y.season, []).append(y.yield_kg_per_ha)
    seasons = sorted(ys_by_season)
    means = [float(np.mean(ys_by_season[s])) for s in seasons]
    base = A.BASELINE_YIELD_KG_HA[crop]
    trend = float(np.polyfit(range(len(means)), means, 1)[0] / base) if len(means) >= 2 else 0.0
    by = {s["scenario"]: s for s in stress}
    ds = by["base_case"]["debt_service_fcfa"]
    return {
        "repayment_rate": on_time / n if n else 0.75,  # population prior when no history (also counted as missing)
        "late_rate": late / n if n else 0.0,
        "default_count": float(sum(r.defaulted for r in rep)),
        "max_days_overdue": float(max((r.days_overdue for r in rep), default=0)),
        "n_loans": float(n),
        "yield_ratio": (float(np.mean(means)) / base) if means else 0.0,
        "yield_cv": float(np.std(means) / np.mean(means)) if len(means) >= 2 else 0.0,
        "yield_trend": trend,
        "area_ha": inp.area_ha,
        "n_seasons": float(len(means)),
        "price_anomaly_pct": float(market.price_anomaly_pct or 0.0),
        "price_volatility": market.volatility,
        "rainfall_anomaly_pct": weather.rainfall_anomaly_pct,
        "drought": float(weather.drought_indicator),
        "log_amount": math.log(inp.proposed_amount_fcfa),
        "obligations_to_debt_service": inp.existing_monthly_obligations_fcfa * inp.tenor_months / ds,
        "base_capacity": float(np.clip(by["base_case"]["repayment_capacity_ratio"], -2, 6)),
        "combined_capacity": float(np.clip(by["combined_shock"]["repayment_capacity_ratio"], -2, 6)),
    }


def assess(case_id: str, case: HouseholdCase, crop: str, package: dict, terms: CreditTerms,
           weather: WeatherSignal, market: MarketSignal, risk_model) -> AssessmentResult:
    if market.latest_price_fcfa_per_kg is None or market.is_conflicting:
        raise AssessmentInputError("no single usable current market price (conflicting observations); refusing to assess")
    if market.is_stale:
        raise AssessmentInputError(f"market price is {market.age_days} days old (stale); refusing to treat it as current")
    if package["crop"] != crop:
        raise AssessmentInputError(f"package {package['package_id']} is for {package['crop']}, not {crop}")

    s = get_settings()
    inp, hist_mean, weather_factor = financial_inputs(case, crop, package, terms, weather, market.latest_price_fcfa_per_kg)
    stress = fin.run_all_stress_cases(inp, s.yield_shock_pct, s.price_shock_pct)
    feats = build_features(case, crop, inp, stress, weather, market)
    pd_, contrib = risk_model.predict(feats)
    base, comb = stress[0], stress[3]
    cost = inp.input_cost_fcfa + inp.other_production_cost_fcfa

    vid = f"{case_id}"
    ev = [
        EvidenceItem(evidence_id=f"derived:{vid}:expected_yield", label="Expected yield", source_type="derived_metric",
                     source_id=f"{vid}:expected_yield", source_uri=f"computed://assess_serviceability/{vid}/expected_yield",
                     retrieval_timestamp=AS_OF, value=f"{inp.expected_yield_kg_per_ha} kg/ha", kind=EvidenceKind.DERIVED,
                     transformation=f"mean of {crop} yield records ({hist_mean:.1f} kg/ha) x package uplift "
                                    f"x rainfall factor {weather_factor:.3f}"),
        EvidenceItem(evidence_id=f"derived:{vid}:repayment_capacity", label="Repayment capacity (base / combined shock)",
                     source_type="derived_metric", source_id=f"{vid}:repayment_capacity",
                     source_uri=f"computed://assess_serviceability/{vid}/stress", retrieval_timestamp=AS_OF,
                     value=f"{base['repayment_capacity_ratio']:.2f}x / {comb['repayment_capacity_ratio']:.2f}x",
                     kind=EvidenceKind.DERIVED,
                     transformation="(gross margin - existing obligations over tenor) / debt service; "
                                    f"shocks: yield -{s.yield_shock_pct:.0%}, price -{s.price_shock_pct:.0%}"),
        EvidenceItem(evidence_id=f"model:{vid}:pd", label="Probability of default", source_type="model_prediction",
                     source_id=f"{risk_model.version}:{vid}", source_uri=f"model://{risk_model.version}",
                     retrieval_timestamp=AS_OF, value=f"{pd_:.3f}", kind=EvidenceKind.MODEL_PREDICTION,
                     transformation="calibrated logistic regression over 18 features (see data/model_card.json)"),
        EvidenceItem(evidence_id=f"package:{package['package_id']}", label="Input package cost", source_type="product_catalogue",
                     source_id=package["package_id"], source_uri=f"sqlite://data/agricredit.db/input_packages/{package['package_id']}",
                     retrieval_timestamp=AS_OF, value=f"{package['name']}: {package['cost_fcfa']:,.0f} FCFA",
                     kind=EvidenceKind.OBSERVED),
    ]
    ev = [signed(e) for e in ev]
    return signed(AssessmentResult(
        case_id=case_id, crop=crop, area_ha=inp.area_ha, expected_yield_kg_per_ha=inp.expected_yield_kg_per_ha,
        expected_revenue_fcfa=base["expected_revenue_fcfa"], expected_cost_fcfa=round(cost, 2),
        expected_gross_margin_fcfa=base["expected_gross_margin_fcfa"], debt_service_fcfa=base["debt_service_fcfa"],
        base_repayment_capacity_ratio=base["repayment_capacity_ratio"],
        stress_repayment_capacity_ratio=comb["repayment_capacity_ratio"],
        stress_results=[StressCaseResult(**x) for x in stress],
        max_serviceable_amount_fcfa=fin.max_serviceable_amount(
            lambda f: financial_inputs(case, crop, package, terms, weather, market.latest_price_fcfa_per_kg, f)[0],
            terms.proposed_amount_fcfa, s.yield_shock_pct, s.price_shock_pct, 1.0),
        probability_of_default=round(pd_, 4), risk_score=fin.risk_score_from_pd(pd_),
        risk_band=RiskBand(fin.risk_band_from_pd(pd_)),
        feature_values={k: round(v, 4) for k, v in feats.items()},
        feature_contributions={k: round(v, 4) for k, v in contrib.items()},
        assumptions=A.describe(crop), risk_model_version=risk_model.version, assessed_at=AS_OF, evidence=ev,
    ))
