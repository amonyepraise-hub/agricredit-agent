"""Real farm sizes and crop mixes from the World Bank / INSAE EHCVM 2021/22 survey for Benin.

    python -m agricredit.opendata ehcvm

Reads three Stata files the user downloads with a free World Bank Microdata account into data/real/ehcvm/
(never committed: the licence forbids redistribution) and writes ONLY department-level aggregates to
data/real/processed/ehcvm_farm_profile.csv: weighted shares of farming households growing each crop and weighted
quantiles of the area each household grows. Cells with fewer than MIN_CELL households are suppressed.
The builder then draws simulated households' crops and plot sizes from these real distributions.

Variable names follow the survey's data dictionary (catalog 6272): household keys `grappe` + `menage`; plots
(s16a) `s16aq08` main crop, `s16aq09a` declared area, `s16aq09b` unit; welfare `departement`, `hhweight`. A
GPS-measured area is used instead of the declared one when a variable labelled as GPS area exists.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import REPO_ROOT

RAW = REPO_ROOT / "data" / "real" / "ehcvm"
OUT = REPO_ROOT / "data" / "real" / "processed" / "ehcvm_farm_profile.csv"
FILES = {"plots": "s16a_me_ben2021.dta", "welfare": "ehcvm_welfare_ben2021.dta"}
MIN_CELL = 20
QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)
CROP_KEYWORDS = {"maize": ("mais",), "rice": ("riz",), "soybean": ("soja",), "cotton": ("coton",),
                 "cashew": ("anacard", "cajou")}
# hectares per unit, matched on the unit's value label (accents removed, lower case)
UNIT_HA = [(r"hectare|\bha\b", 1.0), (r"\bares?\b", 0.01), (r"m2|metre.?carre|metres.?carres|m.?carre", 0.0001)]


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower().strip()


def read_stata(path: Path, label_cols: tuple[str, ...]) -> tuple[pd.DataFrame, dict]:
    """Returns (data, variable labels). Columns in `label_cols` are replaced by their value labels (text)."""
    with pd.read_stata(path, iterator=True, convert_categoricals=False) as r:
        df = r.read()
        var_labels = r.variable_labels()
        value_labels = r.value_labels()
        lbllist = list(getattr(r, "_lbllist", []) or [])
    cols = [c for c in label_cols if c in df.columns]
    try:
        text = pd.read_stata(path, columns=cols, convert_categoricals=True)
        for c in cols:
            df[c] = text[c].astype(str)
    except ValueError:  # non-unique labels: decode by hand
        by_var = dict(zip(df.columns, lbllist)) if lbllist else {}
        for c in cols:
            labels = value_labels.get(by_var.get(c) or c, {})
            df[c] = df[c].map(lambda v, L=labels: "" if pd.isna(v) else (L.get(int(v), str(v))
                              if float(v).is_integer() else str(v)))
    return df, var_labels


def crop_of(label: str) -> str | None:
    f = _fold(label)
    for crop, keys in CROP_KEYWORDS.items():
        if any(k in f for k in keys):
            return crop
    return None


def unit_factor(label: str) -> float | None:
    f = _fold(label)
    for pattern, ha in UNIT_HA:
        if re.search(pattern, f):
            return ha
    return None


def plot_areas(plots: pd.DataFrame, var_labels: dict) -> tuple[pd.Series, str]:
    """Area in hectares per plot, preferring a GPS measurement when the survey has one."""
    gps = [c for c, lab in var_labels.items() if c in plots.columns and "gps" in _fold(lab)
           and re.search(r"superficie|surface|area|aire", _fold(lab))]
    if gps:
        g = pd.to_numeric(plots[gps[0]], errors="coerce")
        lab = _fold(var_labels[gps[0]])
        factor = 1.0 if re.search(r"hectare|\bha\b", lab) else (0.0001 if re.search(r"m2|metre", lab) or g.median() > 50 else 1.0)
        declared, _ = declared_areas(plots)
        area = (g * factor).where(g > 0, declared)
        return area, f"GPS area `{gps[0]}` where measured, otherwise declared area"
    return declared_areas(plots)


def declared_areas(plots: pd.DataFrame) -> tuple[pd.Series, str]:
    qty = pd.to_numeric(plots["s16aq09a"], errors="coerce")
    factors = plots["s16aq09b"].astype(str).map(unit_factor)
    return qty * factors, "declared area `s16aq09a` x unit `s16aq09b` (hectare, are, square metre; local units dropped)"


def weighted_quantiles(values: np.ndarray, weights: np.ndarray, qs=QUANTILES) -> list[float]:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = (np.cumsum(w) - 0.5 * w) / w.sum()
    return [float(np.interp(q, cum, v)) for q in qs]


def build_profile(raw: Path = RAW, out: Path = OUT, log=print) -> pd.DataFrame:
    missing = [f for f in FILES.values() if not (raw / f).exists()]
    if missing:
        raise SystemExit(f"Missing EHCVM files in {raw}: {', '.join(missing)}. See docs/REAL_DATA.md.")
    plots, p_vars = read_stata(raw / FILES["plots"], ("s16aq08", "s16aq09b"))
    welfare, _ = read_stata(raw / FILES["welfare"], ("departement",))
    for col in ("grappe", "menage", "s16aq08", "s16aq09a", "s16aq09b"):
        if col not in plots.columns:
            raise SystemExit(f"{FILES['plots']} has no `{col}` column; the file may be a different survey round.")
    for col in ("grappe", "menage", "departement", "hhweight"):
        if col not in welfare.columns:
            raise SystemExit(f"{FILES['welfare']} has no `{col}` column.")

    plots = plots.copy()
    plots["crop"] = plots["s16aq08"].astype(str).map(crop_of)
    plots["area_ha"], how = plot_areas(plots, p_vars)
    plots = plots[(plots["area_ha"] > 0) & (plots["area_ha"] < 100)]
    w = welfare[["grappe", "menage", "departement", "hhweight"]].copy()
    w["department"] = w["departement"].astype(str)
    hh_area = plots.dropna(subset=["crop"]).groupby(["grappe", "menage", "crop"], as_index=False)["area_ha"].sum()
    farm = plots.groupby(["grappe", "menage"], as_index=False).agg(n_plots=("area_ha", "size"), total_ha=("area_ha", "sum"))
    farm = farm.merge(w, on=["grappe", "menage"], how="inner")
    hh_area = hh_area.merge(w, on=["grappe", "menage"], how="inner")
    log(f"     {len(plots)} plots with a usable area ({how}); {len(farm)} farming households matched to weights")

    rows = []
    groups = [("Benin (national)", farm, hh_area)] + [
        (d, farm[farm["department"] == d], hh_area[hh_area["department"] == d]) for d in sorted(farm["department"].unique())]
    for dept, f_, h_ in groups:
        if len(f_) < MIN_CELL:
            continue
        tot_w = f_["hhweight"].sum()
        row = {"department": dept, "crop": "all", "n_households": len(f_), "share_growing": 1.0,
               "mean_plots": round(float(np.average(f_["n_plots"], weights=f_["hhweight"])), 2)}
        row |= {f"area_p{int(q * 100)}": round(v, 3) for q, v in zip(QUANTILES, weighted_quantiles(
            f_["total_ha"].to_numpy(), f_["hhweight"].to_numpy()))}
        rows.append(row)
        for crop in CROP_KEYWORDS:
            c_ = h_[h_["crop"] == crop]
            if len(c_) < MIN_CELL:
                continue
            row = {"department": dept, "crop": crop, "n_households": len(c_),
                   "share_growing": round(float(c_["hhweight"].sum() / tot_w), 3), "mean_plots": None}
            row |= {f"area_p{int(q * 100)}": round(v, 3) for q, v in zip(QUANTILES, weighted_quantiles(
                c_["area_ha"].to_numpy(), c_["hhweight"].to_numpy()))}
            rows.append(row)
    prof = pd.DataFrame(rows)
    prof["source"] = "EHCVM 2021/22 Benin (INSAE / World Bank), weighted; cells under 20 households suppressed"
    out.parent.mkdir(parents=True, exist_ok=True)
    prof.to_csv(out, index=False)
    log(f"     wrote {len(prof)} department x crop rows to {out}")
    return prof


def sample_area(row: dict, rng: np.random.Generator) -> float:
    """Draw an area from the stored quantiles (piecewise-linear inverse CDF, tails held at p10 / p90)."""
    qs = np.array(QUANTILES)
    vals = np.array([float(row[f"area_p{int(q * 100)}"]) for q in QUANTILES])
    return float(np.interp(rng.random(), qs, vals))
