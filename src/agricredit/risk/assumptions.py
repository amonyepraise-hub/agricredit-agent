"""Documented agronomic and financial assumptions used by the serviceability model.

These are planning assumptions, not observations. Each one is reported back in
the advisory's `assumptions` list so the officer can see and challenge it.
"""

# Non-package production cost per hectare (labour, land preparation, transport, bags), FCFA/ha.
OTHER_PRODUCTION_COST_FCFA_PER_HA = {
    "maize": 45000.0, "cotton": 70000.0, "rice": 95000.0, "cashew": 35000.0, "soybean": 45000.0,
}
# Share of harvest assumed sold (the remainder is retained for household food or seed).
MARKETED_SHARE = {"maize": 0.75, "cotton": 1.00, "rice": 0.75, "cashew": 0.95, "soybean": 0.85}
# Crop baseline yields (kg/ha) used only to normalise yield features across crops.
BASELINE_YIELD_KG_HA = {"maize": 1400.0, "cotton": 900.0, "rice": 2200.0, "cashew": 650.0, "soybean": 1100.0}
# Package uplift is applied at this fraction of the package's nominal agronomic claim.
PACKAGE_UPLIFT_REALISATION = 0.5
# Yield sensitivity to rainfall deficit: each 1% of deficit beyond the tolerance band cuts yield by this %.
RAINFALL_DEFICIT_TOLERANCE_PCT = 10.0
RAINFALL_DEFICIT_YIELD_ELASTICITY = 0.8


def describe(crop: str) -> list[str]:
    return [
        f"Non-package production cost assumed at {OTHER_PRODUCTION_COST_FCFA_PER_HA[crop]:,.0f} FCFA/ha for {crop}.",
        f"{MARKETED_SHARE[crop]:.0%} of the {crop} harvest assumed sold; the rest retained by the household.",
        f"Input-package yield uplift realised at {PACKAGE_UPLIFT_REALISATION:.0%} of the package's nominal claim.",
        f"Rainfall deficit beyond {RAINFALL_DEFICIT_TOLERANCE_PCT:.0f}% reduces expected yield by "
        f"{RAINFALL_DEFICIT_YIELD_ELASTICITY} % per % of additional deficit.",
        "Debt service = principal + flat interest pro-rated over the tenor + upfront fee, repaid from one season's cash.",
        "The loan finances the input package; only the unfinanced part of the package cost is deducted from cash available.",
        "Expected yield = mean of the household's recorded yields for this crop (all plots, completed seasons).",
    ]
