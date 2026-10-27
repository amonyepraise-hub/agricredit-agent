"""Build data/agricredit_real.db from the processed open-data extracts (offline, seeded).

    python -m agricredit.opendata build

What is real: commune boundaries, official commune yields, rainfall (two sources), market prices,
fertiliser prices and doses, and the default-rate calibration target. What is simulated: the
households themselves (placed in real communes, with yields drawn around their commune's official
yields) and their repayment histories. data/real/SOURCES.md says this in full.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np

from ..config import REPO_ROOT
from .fetch import PROCESSED, read_csv
from .geo import haversine_km
from .sources import FERTILISER_PRICE_FCFA_PER_50KG as FERT, FULL_DOSE_YIELD_GAIN, MAIZE_DOSE_KG_PER_HA as DOSE, SOURCES, sources_markdown

DB_PATH = REPO_ROOT / "data" / "agricredit_real.db"
POLICY_BASE = REPO_ROOT / "fs_mcp_root" / "policy" / "agent_policy.json"
POLICY_REAL = REPO_ROOT / "fs_mcp_root" / "policy" / "agent_policy_real.json"
SCHEMA = REPO_ROOT / "data" / "schema.sql"

CASE_CROPS = ("maize", "rice")   # crops with both a priced input package and WFP market prices
CROPS = ["maize", "cotton", "rice", "cashew", "soybean"]
PRODUCTS = ["Seasonal Input Credit", "Cooperative Credit Line", "Extension Office Microloan"]
OFFICERS = [("OFF-001", "A. Houngbo (fictional officer)", "extension_officer"),
            ("OFF-002", "M. Dossou (fictional officer)", "cooperative_credit_desk"),
            ("OFF-003", "K. Agossa (fictional officer)", "extension_officer")]


def iso(d: dt.datetime) -> str:
    return d.replace(microsecond=0).isoformat()


def package_cost(hectares: float) -> float:
    bags_npk = DOSE["NPK"] * hectares / 50
    bags_urea = DOSE["urea"] * hectares / 50
    return round(bags_npk * FERT["NPK"] + bags_urea * FERT["urea"], 0)


def packages() -> list[tuple]:
    out = []
    for crop in CASE_CROPS:
        for code, ha in (("HALF", 0.5), ("1HA", 1.0), ("2HA", 2.0)):
            name = (f"{crop.title()} fertiliser {ha:g} ha: {DOSE['NPK'] * ha / 50:g} x 50 kg NPK + {DOSE['urea'] * ha / 50:g} x 50 kg urea "
                    f"at 2025/26 subsidised prices")
            out.append((f"PKG-{crop.upper()}-{code}", name, crop, package_cost(ha), FULL_DOSE_YIELD_GAIN))
    return out


MIN_CASE_PLOT_HA = 0.4  # smaller plots are not offered a package (the smallest package covers 0.5 ha)


def _pick_package(crop: str, area: float) -> tuple[str, float]:
    """The largest package that does not exceed the plot (never fertiliser for land the household does not farm)."""
    code, ha = ("HALF", 0.5) if area < 1.0 else (("1HA", 1.0) if area < 2.0 else ("2HA", 2.0))
    return f"PKG-{crop.upper()}-{code}", package_cost(ha)


def load_extracts(processed: Path = PROCESSED) -> dict:
    snap_path = processed / "snapshot.json"
    if not snap_path.exists():
        raise SystemExit("data/real/processed/snapshot.json is missing: run `python -m agricredit.opendata fetch` first "
                         "(needs internet).")
    ex = {"snapshot": json.loads(snap_path.read_text())}
    for name in ("communes", "commune_yields", "rainfall_seasons", "rainfall_crosscheck", "market_prices", "markets"):
        ex[name] = read_csv(processed / f"{name}.csv")
    if not ex["market_prices"]:
        raise SystemExit("No WFP market prices in data/real/processed/: re-run the fetch step with internet access.")
    if not ex["rainfall_seasons"]:
        raise SystemExit("No rainfall in data/real/processed/: re-run the fetch step with internet access.")
    return ex


def build(seed: int = 42, households_per_commune: int = 5, db_path: Path = DB_PATH, processed: Path = PROCESSED,
          policy_out: Path = POLICY_REAL, sources_md: Path = REPO_ROOT / "data" / "real" / "SOURCES.md") -> dict:
    ex = load_extracts(processed)
    snap = ex["snapshot"]
    now = dt.datetime.fromisoformat(snap["as_of"])
    season = snap["current_season"]
    rng = np.random.default_rng(seed)

    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript(SCHEMA.read_text())

    # ---- sources, locations, official yields
    cur.executemany("INSERT INTO data_sources VALUES (?,?,?,?,?,?)",
                    [(s.key, s.name, s.publisher, s.licence, s.url, snap["as_of"]) for s in SOURCES])
    communes = {c["location_id"]: c for c in ex["communes"]}
    markets = [m for m in ex["markets"] if m.get("lat") and m.get("lon")]
    for c in communes.values():
        cur.execute("INSERT INTO locations VALUES (?,?,?,?,?,?,?)",
                    (c["location_id"], c["fnid"], c["name"], c["admin_1"], float(c["lat"]), float(c["lon"]), c["zone"]))
    cur.executemany("INSERT INTO commune_yields VALUES (?,?,?,?,?,?,?,?)",
                    [(y["fnid"], y["crop"], int(y["harvest_year"]), float(y["area_ha"]), float(y["production_t"]),
                      float(y["yield_kg_per_ha"]), y["source_dataset"], y["source_url"]) for y in ex["commune_yields"]])
    yields = defaultdict(dict)  # (fnid, crop) -> {year: kg/ha}
    areas = defaultdict(float)  # (fnid, crop) -> latest harvested area
    for y in ex["commune_yields"]:
        yields[(y["fnid"], y["crop"])][int(y["harvest_year"])] = float(y["yield_kg_per_ha"])
        areas[(y["fnid"], y["crop"])] = float(y["area_ha"])

    # ---- weather (primary rows for every crop; ERA5 cross-check rows)
    weather, base_label = {}, "baseline"
    for r in ex["rainfall_seasons"]:
        obs = dt.datetime.fromisoformat(r["last_data_date"]) + dt.timedelta(hours=12)
        signal = (f"main-season window {r['window_start']}..{r['window_end']}"
                  f"{' (complete)' if r['season_complete'] == 'True' else ' (season to date)'}; baseline {r['baseline_years']}")
        for crop in CROPS:
            cur.execute("""INSERT INTO weather_observations (location_id, crop, season, rainfall_mm, rainfall_anomaly_pct,
                           seasonal_baseline_mm, drought_indicator, crop_calendar_signal, observation_timestamp,
                           retrieval_timestamp, source_dataset, source_url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (r["location_id"], crop, r["season"], float(r["rainfall_mm"]), float(r["rainfall_anomaly_pct"]),
                         float(r["seasonal_baseline_mm"]), int(r["drought_indicator"]), f"{crop}: {signal}", iso(obs),
                         snap["as_of"], r["source_dataset"], r["source_url"]))
        if r["season"] == season:
            weather[r["location_id"]] = float(r["rainfall_anomaly_pct"])
            base_label = f"{r['baseline_years']} average"
    for r in ex["rainfall_crosscheck"]:
        obs = dt.datetime.fromisoformat(r["last_data_date"]) + dt.timedelta(hours=12)
        for crop in CROPS:
            cur.execute("""INSERT INTO weather_crosscheck (location_id, crop, season, rainfall_mm, rainfall_anomaly_pct,
                           seasonal_baseline_mm, observation_timestamp, source_dataset, source_url) VALUES (?,?,?,?,?,?,?,?,?)""",
                        (r["location_id"], crop, r["season"], float(r["rainfall_mm"]), float(r["rainfall_anomaly_pct"]),
                         float(r["seasonal_baseline_mm"]), iso(obs), r["source_dataset"], r["source_url"]))

    # ---- market prices
    priced = defaultdict(set)  # market_centre -> crops
    for p in ex["market_prices"]:
        d = dt.datetime.fromisoformat(p["date"]) + dt.timedelta(hours=12)
        cur.execute("""INSERT INTO market_prices (commodity, market_centre, price_fcfa_per_kg, observation_timestamp,
                       retrieval_timestamp, source_dataset, source_url) VALUES (?,?,?,?,?,?,?)""",
                    (p["crop"], p["market_centre"], float(p["price_fcfa_per_kg"]), iso(d), snap["as_of"],
                     p["source_dataset"], p["source_url"]))
        priced[p["market_centre"]].add(p["crop"])

    def nearest_market(loc: dict, crop: str) -> tuple[str, float] | None:
        cands = [(haversine_km(float(loc["lat"]), float(loc["lon"]), float(m["lat"]), float(m["lon"])), m["market_centre"])
                 for m in markets if crop in priced.get(m["market_centre"], set())]
        return (min(cands)[1], round(min(cands)[0], 1)) if cands else None

    # ---- catalogue
    cur.executemany("INSERT INTO input_packages VALUES (?,?,?,?,?)", packages())
    cur.executemany("INSERT INTO loan_products VALUES (?,?,?,?,?)", [
        ("LP-SEASONAL", "Seasonal Input Credit", 12.0, 2.0, 6),
        ("LP-COOP", "Cooperative Credit Line", 9.5, 1.5, 9),
        ("LP-MICRO", "Extension Office Microloan", 15.0, 3.0, 4),
    ])
    cur.executemany("INSERT INTO officers VALUES (?,?,?,1)", OFFICERS)

    # ---- households simulated inside real communes
    usable = [lid for lid in communes if lid in weather]
    hh_index = 10000
    by_commune = defaultdict(list)
    for lid in usable:
        c = communes[lid]
        grown = [(crop, areas[(c["fnid"], crop)]) for crop in CROPS if len(yields[(c["fnid"], crop)]) >= 2]
        if not grown:
            continue
        crops_avail = [g[0] for g in grown]
        weights = np.array([g[1] for g in grown]) / sum(g[1] for g in grown)
        for _ in range(households_per_commune):
            hh_index += 1
            hh = f"HH-{hh_index:05d}"
            reliability = float(np.clip(rng.beta(5, 2), 0.02, 0.99))
            n_plots = min(len(crops_avail), int(rng.integers(1, 4)))
            first = "maize" if "maize" in crops_avail and rng.random() < 0.7 else str(rng.choice(crops_avail, p=weights))
            others = [x for x in rng.choice(crops_avail, size=len(crops_avail), replace=False, p=weights) if x != first]
            plot_crops = [first] + list(others)[: n_plots - 1]
            cur.execute("INSERT INTO households VALUES (?,?,1,?,?)",
                        (hh, lid, f"Simulated household in {c['name']} commune ({c['admin_1']}).",
                         iso(now - dt.timedelta(days=int(rng.integers(200, 1200))))))
            cur.execute("INSERT INTO synthetic_latent_traits VALUES (?,?)", (hh, reliability))
            for p_i, crop in enumerate(plot_crops):
                plot_id = f"{hh}-P{p_i + 1}"
                area = float(np.clip(rng.gamma(2.0, 0.9), 0.3, 6.0))
                cur.execute("INSERT INTO plots VALUES (?,?,?,?,?,?)", (plot_id, hh, crop, round(area, 2), None, lid))
                years = sorted(yields[(c["fnid"], crop)])[-3:]
                for yr in years:
                    commune_y = yields[(c["fnid"], crop)][yr]
                    y = max(50.0, commune_y * (0.55 + 0.6 * reliability) * rng.normal(1.0, 0.11))
                    rec = dt.datetime(yr, 12, 1) + dt.timedelta(days=int(rng.integers(0, 30)))
                    cur.execute("INSERT INTO yield_history (plot_id, household_id, crop, season, yield_kg_per_ha, recorded_at) "
                                "VALUES (?,?,?,?,?,?)", (plot_id, hh, crop, str(yr), round(y, 1), iso(rec)))
            for l_i in range(int(rng.integers(1, 4))):
                amount = float(np.clip(rng.gamma(2.0, 50000), 40000, 300000))
                due = now - dt.timedelta(days=int(720 - l_i * 230 + rng.integers(0, 30)))
                on_time = rng.random() < (0.15 + 0.80 * reliability)
                defaulted = (not on_time) and rng.random() < max(0.0, 0.5 - 0.45 * reliability)
                overdue = 0 if on_time else int(rng.integers(3, 60))
                paid = None if defaulted else due + dt.timedelta(days=overdue)
                cur.execute("INSERT INTO repayment_history VALUES (?,?,?,?,?,?,?,?,?)",
                            (f"LOAN-{hh}-{l_i + 1}", hh, str(rng.choice(PRODUCTS)), round(amount, 2), due.date().isoformat(),
                             paid.date().isoformat() if paid else None, overdue, int(defaulted),
                             iso(now - dt.timedelta(days=int(rng.integers(2, 20))))))
            if rng.random() < 0.3:
                outstanding = float(np.clip(rng.gamma(1.5, 35000), 10000, 180000))
                cur.execute("INSERT INTO current_obligations (household_id, product, outstanding_amount_fcfa, monthly_due_fcfa) "
                            "VALUES (?,?,?,?)", (hh, "Existing microfinance loan", round(outstanding, 2),
                                                 round(outstanding / int(rng.integers(6, 12)), 2)))
            by_commune[lid].append((hh, plot_crops[0]))

    # ---- credit cases: showcase (real conditions) first, then routine
    def first_plot(hh: str):
        return cur.execute("SELECT crop, area_ha FROM plots WHERE plot_id = ?", (hh + "-P1",)).fetchone()

    def latest_vs_usual(centre: str, crop: str) -> tuple[float, str] | None:
        """Latest price vs the median of the same calendar month in earlier years (seasonality-aware)."""
        rows = cur.execute("SELECT price_fcfa_per_kg, observation_timestamp FROM market_prices WHERE market_centre = ? "
                           "AND commodity = ? ORDER BY observation_timestamp DESC", (centre, crop)).fetchall()
        if not rows:
            return None
        latest_p, latest_t = rows[0][0], dt.datetime.fromisoformat(rows[0][1])
        same = [p_ for p_, t_ in rows[1:] if dt.datetime.fromisoformat(t_).month == latest_t.month
                and dt.datetime.fromisoformat(t_).year < latest_t.year]
        if len(same) < 2:
            return None
        return (latest_p / float(np.median(same)) - 1) * 100, latest_t.strftime("%B")

    cases, used_hh = [], set()

    def add_case(lid: str, crop: str, tag: str) -> bool:
        loc = communes[lid]
        mk = nearest_market(loc, crop)
        if mk is None:
            return False
        for hh, c1 in by_commune.get(lid, []):
            if hh in used_hh or c1 != crop:
                continue
            _, area = first_plot(hh)
            if area < MIN_CASE_PLOT_HA:
                continue
            pkg, amount = _pick_package(crop, area)
            used_hh.add(hh)
            cases.append((f"BEN-{2001 + len(cases):04d}", hh, pkg, "LP-SEASONAL", crop, mk[0], season, amount, 6,
                          f"{tag} | {loc['name']} ({loc['admin_1']}); nearest {crop} market {mk[0]} ~{mk[1]:.0f} km"))
            return True
        return False

    maize_communes = [lid for lid in by_commune if any(c == "maize" for _, c in by_commune[lid])]
    if maize_communes:
        driest = sorted(maize_communes, key=lambda k: weather[k])
        low = "driest commune" if weather[driest[0]] < 0 else "lowest rainfall relative to normal"
        add_case(driest[0], "maize", f"REAL rainfall: {low} this season ({weather[driest[0]]:+.1f}% vs {base_label}, ERA5)")
        add_case(driest[-1], "maize", f"REAL rainfall: wettest commune this season ({weather[driest[-1]]:+.1f}%)")
        taken = {driest[0], driest[-1]}
        normal = min((k for k in maize_communes if k not in taken), key=lambda k: abs(weather[k]), default=None)
        if normal:
            add_case(normal, "maize", f"REAL rainfall: closest to normal this season ({weather[normal]:+.1f}%)")
        drops = [(v[0], m["market_centre"], v[1]) for m in markets if "maize" in priced.get(m["market_centre"], set())
                 for v in [latest_vs_usual(m["market_centre"], "maize")] if v is not None]
        if drops:
            v, centre, month = min(drops)
            m = next(x for x in markets if x["market_centre"] == centre)
            near = sorted(maize_communes, key=lambda k: haversine_km(float(communes[k]["lat"]), float(communes[k]["lon"]),
                                                                     float(m["lat"]), float(m["lon"])))
            for k in near[:3]:
                if add_case(k, "maize", f"REAL prices: largest maize price fall vs the usual {month} price ({v:+.1f}% at {centre}, WFP)"):
                    break
    for lid in sorted(by_commune, key=lambda k: weather[k]):
        for crop in ("rice",):
            if any(c == crop for _, c in by_commune[lid]) and add_case(lid, crop, "REAL data: rice-growing commune"):
                break
        else:
            continue
        break
    for lid in sorted(by_commune):
        if len(cases) >= 30:
            break
        add_case(lid, "maize", "REAL data: routine maize case")
    for c in cases:
        cur.execute("INSERT INTO credit_cases VALUES (?,?,?,?,?,?,?,?,?,?,?)", (*c, iso(now - dt.timedelta(days=1))))
    conn.commit()

    # ---- policy for real mode: alternate centre = nearest other WFP market; monthly-price look-back
    policy = json.loads(POLICY_BASE.read_text())
    policy["policy_version"] = policy["policy_version"].replace("-v1", "") + "-real-open-data-v1"
    policy["data_mode"] = "real-open-data"
    alts = {}
    for m in markets:
        others = sorted((haversine_km(float(m["lat"]), float(m["lon"]), float(o["lat"]), float(o["lon"])), o["market_centre"])
                        for o in markets if o["market_centre"] != m["market_centre"])
        if others:
            alts[m["market_centre"]] = others[0][1]
    policy["market"]["alternate_centres"] = alts
    policy["market"]["lookback_weeks"] = 52
    policy["decision"]["allow_deferred_repayment"] = True  # needs the multi-year monthly WFP history
    policy["weather"]["lookback_days"] = 180
    policy_out.write_text(json.dumps(policy, indent=2, ensure_ascii=False))
    sources_md.parent.mkdir(parents=True, exist_ok=True)
    sources_md.write_text(sources_markdown())

    stats = {"db": str(db_path), "as_of": snap["as_of"], "communes_with_rainfall": len(usable),
             "households": hh_index - 10000, "cases": len(cases), "markets": len(markets),
             "showcase": [c[0] + ": " + c[9] for c in cases if not c[9].startswith("REAL data: routine")]}
    conn.close()
    return stats
