import pytest

from agricredit.risk import financial as fin


def _inputs(**kw):
    base = dict(expected_yield_kg_per_ha=1500, area_ha=2.0, price_fcfa_per_kg=180, marketed_share=0.75,
                input_cost_fcfa=95000, other_production_cost_fcfa=90000, proposed_amount_fcfa=95000,
                existing_monthly_obligations_fcfa=0, tenor_months=6, interest_rate_pct=12.0, fee_pct=2.0)
    base.update(kw)
    return fin.FinancialInputs(**base)


def test_expected_revenue_uses_marketed_share():
    assert fin.expected_revenue(1000, 2.0, 150, 0.75) == 225000.0


def test_gross_margin():
    assert fin.expected_gross_margin(300000, 185000) == 115000.0


def test_debt_service():
    # 185,000 at 12%/yr for 6 months = 11,100 interest; 2% fee = 3,700
    assert fin.debt_service(185000, 12.0, 2.0, 6) == 199800.0


def test_debt_service_rejects_zero_tenor():
    with pytest.raises(ValueError):
        fin.debt_service(100, 10, 1, 0)


def test_cash_available_does_not_double_count_financed_inputs():
    # Fully financed package: nothing out of pocket
    assert fin.cash_available_for_debt_service(400000, 90000, 185000, 185000) == 310000
    # Partly financed: the unfinanced 85,000 is deducted
    assert fin.cash_available_for_debt_service(400000, 90000, 185000, 100000) == 225000


def test_capacity_nets_existing_obligations():
    assert fin.repayment_capacity_ratio(200000, 100000, 0, 6) == 2.0
    assert fin.repayment_capacity_ratio(200000, 100000, 10000, 6) == 1.4


def test_dscr():
    assert fin.debt_service_coverage_ratio(100000, 50000) == 2.0


def test_shocks():
    assert fin.apply_yield_shock(1000, 0.20) == 800.0
    assert fin.apply_price_shock(150, 0.10) == 135.0


def test_stress_cases_order_and_monotonicity():
    r = {x["scenario"]: x for x in fin.run_all_stress_cases(_inputs(), 0.20, 0.10)}
    assert list(r) == ["base_case", "yield_shock", "price_shock", "combined_shock"]
    assert r["combined_shock"]["repayment_capacity_ratio"] < r["yield_shock"]["repayment_capacity_ratio"]
    assert r["combined_shock"]["repayment_capacity_ratio"] < r["price_shock"]["repayment_capacity_ratio"]
    assert r["base_case"]["repayment_capacity_ratio"] > r["price_shock"]["repayment_capacity_ratio"]
    assert len({x["debt_service_fcfa"] for x in r.values()}) == 1


def test_shock_sizes_are_configurable():
    mild = fin.run_all_stress_cases(_inputs(), 0.05, 0.05)[3]["repayment_capacity_ratio"]
    harsh = fin.run_all_stress_cases(_inputs(), 0.40, 0.30)[3]["repayment_capacity_ratio"]
    assert harsh < mild


def test_max_serviceable_amount_scales_package_and_loan():
    req = 400000

    def build(f):
        return _inputs(proposed_amount_fcfa=req * f, input_cost_fcfa=260000 * f)

    amt = fin.max_serviceable_amount(build, req, 0.20, 0.10)
    assert 0 <= amt <= req and amt % 5000 == 0
    assert amt > 0
    f = amt / req
    assert fin.run_all_stress_cases(build(f), 0.20, 0.10)[3]["repayment_capacity_ratio"] >= 0.99


def test_max_serviceable_zero_when_nothing_works():
    def build(f):
        return _inputs(expected_yield_kg_per_ha=50, proposed_amount_fcfa=200000 * f, input_cost_fcfa=200000 * f)

    assert fin.max_serviceable_amount(build, 200000, 0.20, 0.10) == 0.0


@pytest.mark.parametrize("pd,band", [(0.0, "LOW"), (0.05, "LOW"), (0.15, "MODERATE"), (0.25, "ELEVATED"), (0.6, "HIGH")])
def test_score_and_band_agree(pd, band):
    score = fin.risk_score_from_pd(pd)
    assert fin.risk_band_from_pd(pd) == band
    lo, hi = {"LOW": (80, 100), "MODERATE": (60, 79), "ELEVATED": (40, 59), "HIGH": (0, 39)}[band]
    assert lo <= score <= hi


def test_score_monotonic():
    scores = [fin.risk_score_from_pd(p / 100) for p in range(0, 101)]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == 100 and scores[-1] == 0
