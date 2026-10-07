"""Deterministic financial calculations.

Plain arithmetic only. The LLM never computes any of these values; it reads
them from the assess_serviceability tool's structured output. This module has
no dependency on the agent, the LLM client or MCP.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np


@dataclass(frozen=True)
class FinancialInputs:
    expected_yield_kg_per_ha: float
    area_ha: float
    price_fcfa_per_kg: float
    marketed_share: float           # share of harvest sold (rest retained for household food)
    input_cost_fcfa: float          # input package cost
    other_production_cost_fcfa: float  # labour, transport, land prep (crop-specific per-ha assumption x area)
    proposed_amount_fcfa: float
    existing_monthly_obligations_fcfa: float
    tenor_months: int
    interest_rate_pct: float
    fee_pct: float


def expected_revenue(yield_kg_per_ha: float, area_ha: float, price_fcfa_per_kg: float, marketed_share: float = 1.0) -> float:
    """Expected Revenue = yield (kg/ha) x area (ha) x marketed share x price (FCFA/kg)."""
    return round(yield_kg_per_ha * area_ha * marketed_share * price_fcfa_per_kg, 2)


def expected_gross_margin(revenue_fcfa: float, cost_fcfa: float) -> float:
    """Expected Gross Margin = Expected Revenue - total production cost."""
    return round(revenue_fcfa - cost_fcfa, 2)


def debt_service(proposed_amount_fcfa: float, interest_rate_pct: float, fee_pct: float, tenor_months: int) -> float:
    """Total owed over the tenor: principal + flat interest pro-rated by tenor + upfront fee.
    Seasonal input credit is typically repaid as a post-harvest bullet, so the
    serviceability test compares the season's margin with the whole amount owed."""
    if tenor_months <= 0:
        raise ValueError("tenor_months must be > 0")
    interest = proposed_amount_fcfa * (interest_rate_pct / 100.0) * (tenor_months / 12.0)
    fees = proposed_amount_fcfa * (fee_pct / 100.0)
    return round(proposed_amount_fcfa + interest + fees, 2)


def cash_available_for_debt_service(revenue_fcfa: float, other_production_cost_fcfa: float,
                                    input_cost_fcfa: float, proposed_amount_fcfa: float) -> float:
    """Cash left to repay the loan = revenue - costs NOT financed by this loan.
    The loan pays for the input package (up to the amount borrowed), so the financed
    part of the package is repaid through debt service rather than deducted twice."""
    out_of_pocket_inputs = max(0.0, input_cost_fcfa - proposed_amount_fcfa)
    return round(revenue_fcfa - other_production_cost_fcfa - out_of_pocket_inputs, 2)


def repayment_capacity_ratio(cash_available_fcfa: float, debt_service_fcfa: float,
                             existing_monthly_obligations_fcfa: float, tenor_months: int) -> float:
    """Repayment Capacity = (cash available for debt service - existing obligations over the tenor) / debt service.
    >= 1.0 means the season's expected cash, after existing commitments, covers the new debt in full."""
    if debt_service_fcfa <= 0:
        raise ValueError("debt_service_fcfa must be > 0")
    available = cash_available_fcfa - existing_monthly_obligations_fcfa * tenor_months
    return round(available / debt_service_fcfa, 4)


def debt_service_coverage_ratio(cash_available_fcfa: float, debt_service_fcfa: float) -> float:
    """DSCR = cash available for debt service / debt service (before netting other obligations)."""
    if debt_service_fcfa <= 0:
        raise ValueError("debt_service_fcfa must be > 0")
    return round(cash_available_fcfa / debt_service_fcfa, 4)


def apply_yield_shock(yield_kg_per_ha: float, shock_pct: float) -> float:
    """shock_pct is a positive fraction: 0.20 means a 20% decline."""
    return round(yield_kg_per_ha * (1.0 - shock_pct), 4)


def apply_price_shock(price_fcfa_per_kg: float, shock_pct: float) -> float:
    return round(price_fcfa_per_kg * (1.0 - shock_pct), 4)


def _scenario(name: str, y: float, p: float, inp: FinancialInputs, ds: float) -> dict:
    revenue = expected_revenue(y, inp.area_ha, p, inp.marketed_share)
    cost = inp.input_cost_fcfa + inp.other_production_cost_fcfa
    margin = expected_gross_margin(revenue, cost)
    cash = cash_available_for_debt_service(revenue, inp.other_production_cost_fcfa, inp.input_cost_fcfa, inp.proposed_amount_fcfa)
    return {
        "scenario": name,
        "yield_kg_per_ha": round(y, 2),
        "price_fcfa_per_kg": round(p, 2),
        "expected_revenue_fcfa": revenue,
        "expected_gross_margin_fcfa": margin,
        "debt_service_fcfa": ds,
        "repayment_capacity_ratio": repayment_capacity_ratio(cash, ds, inp.existing_monthly_obligations_fcfa, inp.tenor_months),
        "dscr": debt_service_coverage_ratio(cash, ds),
    }


def run_all_stress_cases(inp: FinancialInputs, yield_shock_pct: float, price_shock_pct: float) -> list[dict]:
    """BASE_CASE, YIELD_SHOCK, PRICE_SHOCK, COMBINED_SHOCK. Shock sizes are configurable."""
    ds = debt_service(inp.proposed_amount_fcfa, inp.interest_rate_pct, inp.fee_pct, inp.tenor_months)
    y, p = inp.expected_yield_kg_per_ha, inp.price_fcfa_per_kg
    ys, ps = apply_yield_shock(y, yield_shock_pct), apply_price_shock(p, price_shock_pct)
    return [
        _scenario("base_case", y, p, inp, ds),
        _scenario("yield_shock", ys, p, inp, ds),
        _scenario("price_shock", y, ps, inp, ds),
        _scenario("combined_shock", ys, ps, inp, ds),
    ]


def max_serviceable_amount(build_inputs: Callable[[float], FinancialInputs], requested_amount_fcfa: float,
                           yield_shock_pct: float, price_shock_pct: float, min_combined_capacity: float = 1.0,
                           step_fraction: float = 0.05, round_to: float = 5000.0) -> float:
    """Largest scaled-down offer whose COMBINED-shock repayment capacity stays >= min_combined_capacity.

    A reduced offer means a proportionally smaller input package financed by a proportionally smaller
    loan: `build_inputs(fraction)` returns the FinancialInputs for that fraction of the requested package.
    Deterministic scan over fractions; returns 0.0 if no fraction is serviceable."""
    best = 0.0
    k = 1
    while k * step_fraction <= 1.0 + 1e-9:
        f = round(k * step_fraction, 4)
        inp = build_inputs(f)
        if run_all_stress_cases(inp, yield_shock_pct, price_shock_pct)[3]["repayment_capacity_ratio"] >= min_combined_capacity:
            best = f
        k += 1
    return float(int(requested_amount_fcfa * best // round_to) * round_to)


# PD knots aligned with the risk bands so score and band never disagree:
# LOW (score >= 80) <-> PD < 0.10; MODERATE (60-79) <-> PD < 0.20; ELEVATED (40-59) <-> PD < 0.35; HIGH (< 40).
_PD_KNOTS = [0.0, 0.10, 0.20, 0.35, 1.0]
_SCORE_KNOTS = [100.0, 80.0, 60.0, 40.0, 0.0]


def risk_score_from_pd(probability_of_default: float) -> int:
    """0-100 score, higher is better: piecewise-linear in PD between the band knots,
    then capped at the top of its band so a score never implies a better band than the PD."""
    pd = min(max(probability_of_default, 0.0), 1.0)
    score = float(np.interp(pd, _PD_KNOTS, _SCORE_KNOTS))
    band_cap = {"LOW": 100, "MODERATE": 79, "ELEVATED": 59, "HIGH": 39}[risk_band_from_pd(pd)]
    return int(min(score, band_cap))


def risk_band_from_pd(probability_of_default: float) -> str:
    if probability_of_default < 0.10:
        return "LOW"
    if probability_of_default < 0.20:
        return "MODERATE"
    if probability_of_default < 0.35:
        return "ELEVATED"
    return "HIGH"
