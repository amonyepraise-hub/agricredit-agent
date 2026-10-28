"""Download every open source and write small, reviewable CSV extracts to data/real/processed/.

    python -m agricredit.opendata fetch            # needs internet; about 10-20 minutes the first time
    python -m agricredit.opendata fetch --refresh  # ignore the download cache

Raw downloads are cached in data/real/cache/ (not committed). The processed CSVs plus snapshot.json
are what the database builder reads, so the demo can run offline once they exist.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import time
import unicodedata
from pathlib import Path
from typing import Callable

from ..config import REPO_ROOT
from . import harveststat, prices, rainfall
from .geo import haversine_km
from .http import FetchError

REAL = REPO_ROOT / "data" / "real"
CACHE = REAL / "cache"
PROCESSED = REAL / "processed"


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def locate_markets(markets: list[dict], communes: list[dict]) -> list[dict]:
    """Markets without coordinates get the centroid of the commune whose name appears in the market name."""
    for m in markets:
        if m["lat"] and m["lon"]:
            m["located_by"] = "WFP coordinates"
            continue
        hit = next((c for c in communes if _fold(c["name"]) in _fold(m["market"])), None)
        if hit:
            m["lat"], m["lon"], m["located_by"] = hit["lat"], hit["lon"], f"commune centroid ({hit['name']})"
        else:
            m["located_by"] = "unlocated"
    return markets


def crosscheck_priority(communes: list[dict], primary: list[dict], markets: list[dict], season: str, limit: int) -> list[dict]:
    """Which communes get the (rate-limited) second rainfall source: the driest and wettest this season,
    then those nearest each market, so every showcase case has an independent check."""
    cur = {r["location_id"]: float(r["rainfall_anomaly_pct"]) for r in primary if r["season"] == season}
    ranked = sorted(cur, key=lambda k: cur[k])
    chosen = ranked[:8] + ranked[-4:]
    for m in markets:
        if m.get("lat") and m.get("lon"):
            near = sorted((c for c in communes if c["location_id"] in cur),
                          key=lambda c: haversine_km(float(c["lat"]), float(c["lon"]), float(m["lat"]), float(m["lon"])))
            chosen += [c["location_id"] for c in near[:2]]
    seen, out = set(), []
    by_id = {c["location_id"]: c for c in communes}
    for k in chosen:
        if k not in seen and k in by_id:
            seen.add(k)
            out.append(by_id[k])
    return out[:limit]


def fetch_all(refresh: bool = False, crosscheck_limit: int = 0, log: Callable[[str], None] = print) -> dict:
    t0 = time.time()
    as_of = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    previous = PROCESSED / "snapshot.json"
    if previous.exists() and not refresh:
        # Re-running without --refresh keeps the previous data clock, so every earlier download is reused from the cache.
        as_of = dt.datetime.fromisoformat(json.loads(previous.read_text())["as_of"])
    season = str(as_of.year)
    snap = {"as_of": as_of.isoformat(), "current_season": season, "sources": {}, "warnings": []}

    log("1/5  HarvestStat Africa: commune boundaries (77 communes) ...")
    communes = harveststat.fetch_communes(CACHE)
    write_csv(PROCESSED / "communes.csv", communes)
    snap["sources"]["harveststat_boundaries"] = {"rows": len(communes), "retrieved_at": as_of.isoformat()}

    log("2/5  HarvestStat Africa: official commune crop yields ...")
    yields = harveststat.fetch_yields(CACHE)
    write_csv(PROCESSED / "commune_yields.csv", yields)
    snap["sources"]["harveststat_yields"] = {"rows": len(yields), "latest_year": max(y["harvest_year"] for y in yields),
                                            "retrieved_at": as_of.isoformat()}
    log(f"     {len(yields)} commune-crop-year yields, latest harvest year {snap['sources']['harveststat_yields']['latest_year']}")

    log("3/5  WFP market prices (HDX) ...")
    markets: list[dict] = []
    try:
        price_rows, markets = prices.fetch_prices(CACHE, since=as_of.date() - dt.timedelta(days=5 * 365), refresh=refresh)
        markets = locate_markets(markets, communes)
        write_csv(PROCESSED / "market_prices.csv", price_rows)
        write_csv(PROCESSED / "markets.csv", markets)
        latest = max(r["date"] for r in price_rows)
        snap["sources"]["wfp_hdx"] = {"rows": len(price_rows), "markets": len(markets), "latest_date": latest,
                                     "retrieved_at": as_of.isoformat()}
        log(f"     {len(price_rows)} monthly prices in {len(markets)} markets, latest {latest}")
    except FetchError as e:
        snap["warnings"].append(f"WFP prices not fetched: {e}")
        log(f"     WARNING: {e}")

    log("4/5  ERA5 daily rainfall (Open-Meteo) for every commune: primary source, one request per season per grid cell ...")
    last_day = as_of.date() - dt.timedelta(days=1)
    seasons = [str(y) for y in range(as_of.year - 3, as_of.year + 1)]
    years = sorted(set(range(rainfall.PRIMARY_BASELINE_YEARS[0], rainfall.PRIMARY_BASELINE_YEARS[1] + 1)) | {int(x) for x in seasons})
    primary, cells, fails = [], {}, 0
    for c in communes:  # group communes into 0.5-degree cells so each cell is downloaded once
        cells.setdefault((round(float(c["lat"]) / 0.5), round(float(c["lon"]) / 0.5)), []).append(c)
    for i, members in enumerate(cells.values(), start=1):
        if fails >= 3 and not primary:
            snap["warnings"].append("Open-Meteo unreachable: stopped after 3 failed grid cells")
            log("     Open-Meteo looks unreachable; stopping this step (check the internet connection and re-run).")
            break
        rep = members[0]
        lat, lon = float(rep["lat"]), float(rep["lon"])
        try:
            daily = rainfall.fetch_openmeteo_seasons(lat, lon, rep["zone"], years, last_day, CACHE, refresh=refresh)
        except FetchError as e:
            fails += 1
            snap["warnings"].append(f"Open-Meteo failed near {rep['name']}: {e}")
            log(f"     WARNING near {rep['name']}: {e}")
            continue
        for c in members:
            for s_ in seasons:
                summ = rainfall.season_summary(daily, rep["zone"], int(s_), rainfall.PRIMARY_BASELINE_YEARS, min_years=8)
                if summ and summ["season_complete"]:
                    # only season windows are downloaded; a finished season's total is final as of this download
                    summ["last_data_date"] = last_day.isoformat()
                if summ:
                    primary.append({"location_id": c["location_id"], "source": "primary", "source_dataset": rainfall.ERA5_SOURCE,
                                    "source_url": rainfall.openmeteo_url(lat, lon, dt.date.fromisoformat(summ["window_start"]),
                                                                         dt.date.fromisoformat(summ["window_end"]))} | summ)
        if i % 5 == 0:
            log(f"     {i}/{len(cells)} grid cells")
    write_csv(PROCESSED / "rainfall_seasons.csv", primary)
    snap["sources"]["era5_open_meteo"] = {"rows": len(primary), "grid_cells": len(cells),
                                          "last_data_date": max((r["last_data_date"] for r in primary), default=None),
                                          "retrieved_at": as_of.isoformat()}

    log("5/5  NASA POWER daily rainfall for every commune: independent cross-check ...")
    start = dt.date(rainfall.BASELINE_YEARS[0], 1, 1)
    cross, cell_cache, fails = [], {}, 0
    for c in communes:
        if fails >= 3 and not cross:
            snap["warnings"].append("NASA POWER unreachable: cross-check skipped")
            log("     NASA POWER looks unreachable; the agent will run without the second rainfall source.")
            break
        lat, lon = float(c["lat"]), float(c["lon"])
        cell = (round(lat / 0.5), round(lon / 0.625))
        try:
            if cell not in cell_cache:
                cell_cache[cell] = rainfall.fetch_power(lat, lon, start, last_day, CACHE, refresh=refresh)
        except FetchError as e:
            fails += 1
            snap["warnings"].append(f"NASA POWER failed for {c['name']}: {e}")
            log(f"     WARNING {c['name']}: {e}")
            continue
        summ = rainfall.season_summary(cell_cache[cell], c["zone"], as_of.year)
        if summ:
            cross.append({"location_id": c["location_id"], "source": "crosscheck", "source_dataset": rainfall.POWER_SOURCE,
                          "source_url": rainfall.power_url(lat, lon, start, last_day)} | summ)
    write_csv(PROCESSED / "rainfall_crosscheck.csv", cross)
    snap["sources"]["nasa_power"] = {"rows": len(cross), "grid_cells": len(cell_cache), "retrieved_at": as_of.isoformat()}

    snap["elapsed_s"] = round(time.time() - t0, 1)
    (PROCESSED / "snapshot.json").write_text(json.dumps(snap, indent=2))
    log(f"Done in {snap['elapsed_s']:.0f} s. Extracts written to {PROCESSED}/")
    for w in snap["warnings"]:
        log("  warning: " + w)
    return snap
