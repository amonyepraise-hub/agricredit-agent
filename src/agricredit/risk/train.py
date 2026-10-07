"""Train the PD model on SYNTHETIC outcomes and benchmark it.

    python -m agricredit.risk.train

Labels come from a documented synthetic outcome simulator: each household has a
latent reliability trait (written by the data generator, never a model feature)
and a hypothetical next-season loan outcome is drawn from
    logit P(default) = -2.6 + 4.5*(0.75 - reliability) + 1.3*[base capacity < 1]
                       + 0.6*[combined-shock capacity < 1] + 0.7*drought - 0.4*min(base capacity - 1, 2)
The model only sees observable features. Planted evaluation households are
excluded from training to avoid leaking their outcomes into evaluation.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import RepeatedStratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .. import db, services
from ..config import REPO_ROOT, get_settings
from ..models.schemas import CreditTerms, MarketRequest, WeatherRequest
from . import financial as fin
from .assess import DATA_QUALITY_SIGNALS, FEATURES, build_features, financial_inputs

EXCLUDED = {"HH-00001", "HH-00002", "HH-00004", "HH-00005", "HH-00006", "HH-00007", "HH-00008",
            "HH-00010", "HH-00025", "HH-00099", "HH-00421"}
VERSION = "pd-logreg-calibrated-v1"
CENTRES = ["Bohicon-Market", "Parakou-Market", "Glazoue-Market", "Dassa-Market", "Malanville-Market"]


def _ece(y, p, bins=10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    e = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi)
        if m.any():
            e += m.mean() * abs(y[m].mean() - p[m].mean())
    return float(e)


def build_dataset(seed: int = 7):
    rng = np.random.default_rng(seed)
    s = get_settings()
    with db.read_conn() as c:
        latent = {r["household_id"]: r["reliability"] for r in c.execute("SELECT * FROM synthetic_latent_traits")}
        packages = [dict(r) for r in c.execute("SELECT * FROM input_packages")]
    end = services._now().date()
    start = end - dt.timedelta(weeks=26)
    market_cache, weather_cache = {}, {}
    X, y = [], []
    for hh, rel in sorted(latent.items()):
        if hh in EXCLUDED:
            continue
        case = services.get_household_case(hh)
        for plot in case.plots:
            crop = plot.crop
            pkgs = [p for p in packages if p["crop"] == crop]
            if not pkgs or plot.area_ha < 0.8:
                continue  # applications on very small plots are screened out before credit review
            key = (case.location_id, crop)
            if key not in weather_cache:
                weather_cache[key] = services.get_weather_signal(WeatherRequest(
                    location_id=case.location_id, crop=crop, season="2026", start_date=end - dt.timedelta(days=120), end_date=end))
            for _ in range(4):
                centre = CENTRES[int(rng.integers(len(CENTRES)))]
                if (crop, centre) not in market_cache:
                    market_cache[(crop, centre)] = services.get_market_signal(MarketRequest(
                        commodity=crop, market_centre=centre, start_date=start, end_date=end))
                weather, market = weather_cache[key], market_cache[(crop, centre)]
                if market.latest_price_fcfa_per_kg is None:
                    continue  # conflicting feeds: no usable price, same as at inference time
                price = market.latest_price_fcfa_per_kg
                pkg = pkgs[int(rng.integers(len(pkgs)))]
                amount = float(pkg["cost_fcfa"] * rng.uniform(0.6, 1.3))
                terms = CreditTerms(package_id=pkg["package_id"], product_id="LP-SEASONAL", proposed_amount_fcfa=amount,
                                    tenor_months=6, interest_rate_pct=12.0, fee_pct=2.0)
                inp, _, _ = financial_inputs(case, crop, pkg, terms, weather, price)
                stress = fin.run_all_stress_cases(inp, s.yield_shock_pct, s.price_shock_pct)
                f = build_features(case, crop, inp, stress, weather, market)
                bc, cc = stress[0]["repayment_capacity_ratio"], stress[3]["repayment_capacity_ratio"]
                logit = (-2.6 + 4.5 * (0.75 - rel) + 1.3 * (bc < 1) + 0.6 * (cc < 1)
                         + 0.7 * weather.drought_indicator - 0.4 * min(max(bc - 1, 0), 2))
                X.append([f[k] for k in FEATURES])
                y.append(int(rng.random() < 1 / (1 + math.exp(-logit))))
    return np.array(X, dtype=float), np.array(y)


def main() -> None:
    X, y = build_dataset()

    def lr_pipe():
        return Pipeline([("scale", StandardScaler()), ("lr", LogisticRegression(C=0.5, max_iter=5000))])

    def make(name):
        if name == "logistic_regression_calibrated":
            return CalibratedClassifierCV(lr_pipe(), method="sigmoid", cv=5)
        return CalibratedClassifierCV(HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=250,
                                                                     random_state=0), method="isotonic", cv=5)

    names = ["logistic_regression_calibrated", "gradient_boosting_calibrated"]
    folds = RepeatedStratifiedKFold(n_splits=5, n_repeats=3, random_state=0)
    scores = {n: {"auc": [], "brier": [], "ece": []} for n in names}
    for tr, te in folds.split(X, y):
        for n in names:
            p = make(n).fit(X[tr], y[tr]).predict_proba(X[te])[:, 1]
            scores[n]["auc"].append(roc_auc_score(y[te], p))
            scores[n]["brier"].append(brier_score_loss(y[te], p))
            scores[n]["ece"].append(_ece(y[te], p))
    metrics = {n: {f"{k}_mean": round(float(np.mean(v)), 4) for k, v in d.items()}
               | {f"{k}_std": round(float(np.std(v)), 4) for k, v in d.items()} for n, d in scores.items()}

    lr_m, gb_m = metrics[names[0]], metrics[names[1]]
    choose_lr = lr_m["auc_mean"] >= gb_m["auc_mean"] - 0.02 and lr_m["brier_mean"] <= gb_m["brier_mean"] + 0.01
    rule = ("Prefer the calibrated logistic regression (exact per-feature log-odds explanations) unless gradient "
            "boosting beats it by more than 0.02 mean AUC or 0.01 mean Brier over 5x3 repeated stratified CV.")

    final = make(names[0]).fit(X, y)
    explainer = lr_pipe().fit(X, y)
    joblib.dump({"calibrated": final, "explainer": explainer, "version": VERSION, "features": FEATURES},
                Path(get_settings().risk_model_path))

    card = {
        "model_version": VERSION,
        "production_choice": names[0],
        "selection_rule": rule,
        "selection_rule_satisfied_by_lr": bool(choose_lr),
        "note": None if choose_lr else ("Gradient boosting exceeded the tolerance. Logistic regression is still used in "
                                        "production for exact explanations; this is a recorded trade-off, see EVALS.md."),
        "cv_metrics": metrics,
        "cv_scheme": "RepeatedStratifiedKFold(n_splits=5, n_repeats=3)",
        "n_samples": int(len(y)), "default_rate": round(float(y.mean()), 4),
        "features": FEATURES,
        "data_quality_signals_handled_by_policy_not_model": DATA_QUALITY_SIGNALS,
        "label_source": "SYNTHETIC outcome simulator in risk/train.py (latent reliability + capacity + drought). "
                        "Metrics measure recovery of a known synthetic process, NOT real-world predictive performance.",
        "excluded_households": sorted(EXCLUDED),
    }
    (REPO_ROOT / "data" / "model_card.json").write_text(json.dumps(card, indent=2))
    print(json.dumps({k: card[k] for k in ["production_choice", "selection_rule_satisfied_by_lr", "cv_metrics",
                                            "n_samples", "default_rate"]}, indent=2))


if __name__ == "__main__":
    main()
