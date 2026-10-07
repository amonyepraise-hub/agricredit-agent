"""Loads the trained risk model and produces PD + per-feature log-odds contributions."""
from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np

from ..config import get_settings
from .assess import FEATURES


class RiskModel:
    def __init__(self, bundle: dict):
        self.calibrated = bundle["calibrated"]
        self.explainer = bundle["explainer"]   # StandardScaler + LogisticRegression fitted on all training data
        self.version = bundle["version"]
        self.features = bundle["features"]
        if self.features != FEATURES:
            raise RuntimeError("risk model feature list does not match code; retrain with `make train`")

    def predict(self, feats: dict[str, float]) -> tuple[float, dict[str, float]]:
        x = np.array([[feats[f] for f in self.features]], dtype=float)
        pd_ = float(self.calibrated.predict_proba(x)[0, 1])
        scaler = self.explainer.named_steps["scale"]
        lr = self.explainer.named_steps["lr"]
        z = scaler.transform(x)[0]
        contrib = {f: float(c * v) for f, c, v in zip(self.features, lr.coef_[0], z)}
        return pd_, contrib


_CACHE: dict[str, RiskModel] = {}


def load_risk_model(path: str | None = None) -> RiskModel:
    p = path or get_settings().risk_model_path
    if p not in _CACHE:
        if not Path(p).exists():
            raise FileNotFoundError(f"risk model not found at {p}; run `make train`")
        _CACHE[p] = RiskModel(joblib.load(p))
    return _CACHE[p]
