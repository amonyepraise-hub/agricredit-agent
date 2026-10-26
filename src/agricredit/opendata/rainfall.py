"""Daily rainfall from two independent open sources, summarised per commune and season.

Primary:     NASA POWER daily point API, PRECTOTCORR (bias-corrected precipitation, mm/day).
Cross-check: Open-Meteo historical API, ERA5 reanalysis, precipitation_sum (mm/day).

For each commune centroid the season window depends on the agro-climatic zone (approximate crop
calendar). Season rainfall is compared with the average of the same calendar window over a baseline
period; a deficit of 20% or more is flagged as drought, the same rule the agent already used.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .http import FetchError, get_json
from .sources import OPEN_METEO_ARCHIVE, POWER_DAILY_POINT

# zone -> ((start month, day), (end month, day)) of the main rainy season used for credit review
SEASON_WINDOWS = {
    "south": ((3, 15), (7, 31)),   # major season of the bimodal south
    "centre": ((4, 1), (9, 30)),
    "north": ((5, 15), (10, 15)),  # single season of the north
}
BASELINE_YEARS = (1991, 2020)      # WMO standard normal period
CROSSCHECK_BASELINE_YEARS = (2001, 2020)  # shorter, to stay inside Open-Meteo's free daily allowance
DROUGHT_ANOMALY_PCT = -20.0
MIN_COMPLETENESS = 0.95

POWER_SOURCE = "NASA POWER PRECTOTCORR (daily, bias-corrected)"
ERA5_SOURCE = "ERA5 reanalysis via Open-Meteo (daily precipitation_sum)"


def power_url(lat: float, lon: float, start: dt.date, end: dt.date) -> str:
    return (f"{POWER_DAILY_POINT}?parameters=PRECTOTCORR&community=AG&latitude={lat:.4f}&longitude={lon:.4f}"
            f"&start={start:%Y%m%d}&end={end:%Y%m%d}&format=JSON")


def parse_power(payload: dict) -> dict[dt.date, float]:
    try:
        series = payload["properties"]["parameter"]["PRECTOTCORR"]
    except (KeyError, TypeError) as e:
        raise FetchError(f"unexpected NASA POWER response shape: {str(payload)[:300]}") from e
    fill = float(payload.get("header", {}).get("fill_value", -999.0))
    out = {}
    for k, v in series.items():
        if v is None or float(v) == fill or float(v) < 0:
            continue
        out[dt.date(int(k[:4]), int(k[4:6]), int(k[6:8]))] = float(v)
    return out


def fetch_power(lat: float, lon: float, start: dt.date, end: dt.date, cache: Path, refresh: bool = False) -> dict[dt.date, float]:
    params = {"parameters": "PRECTOTCORR", "community": "AG", "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
              "start": f"{start:%Y%m%d}", "end": f"{end:%Y%m%d}", "format": "JSON"}
    return parse_power(get_json(POWER_DAILY_POINT, cache, params, refresh=refresh))


def openmeteo_url(lat: float, lon: float, start: dt.date, end: dt.date) -> str:
    return (f"{OPEN_METEO_ARCHIVE}?latitude={lat:.4f}&longitude={lon:.4f}&start_date={start}&end_date={end}"
            f"&daily=precipitation_sum&timezone=GMT")


def parse_openmeteo(payload: dict) -> dict[dt.date, float]:
    try:
        times, vals = payload["daily"]["time"], payload["daily"]["precipitation_sum"]
    except (KeyError, TypeError) as e:
        raise FetchError(f"unexpected Open-Meteo response shape: {str(payload)[:300]}") from e
    return {dt.date.fromisoformat(t): float(v) for t, v in zip(times, vals) if v is not None}


def fetch_openmeteo(lat: float, lon: float, start: dt.date, end: dt.date, cache: Path, refresh: bool = False) -> dict[dt.date, float]:
    params = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "start_date": start.isoformat(),
              "end_date": end.isoformat(), "daily": "precipitation_sum", "timezone": "GMT"}
    return parse_openmeteo(get_json(OPEN_METEO_ARCHIVE, cache, params, refresh=refresh))


def fetch_openmeteo_seasons(lat: float, lon: float, zone: str, years: list[int], last_day: dt.date, cache: Path,
                            refresh: bool = False) -> dict[dt.date, float]:
    """Only the season window of each year is requested, which keeps the request volume small."""
    (sm, sd), (em, ed) = SEASON_WINDOWS[zone]
    out: dict[dt.date, float] = {}
    for y in years:
        start, end = dt.date(y, sm, sd), min(dt.date(y, em, ed), last_day)
        if end < start:
            continue
        out.update(fetch_openmeteo(lat, lon, start, end, cache, refresh=refresh and y == years[-1]))
    return out


def _window_total(daily: dict[dt.date, float], start: dt.date, end: dt.date) -> tuple[float, float]:
    n = (end - start).days + 1
    got = [daily[start + dt.timedelta(days=i)] for i in range(n) if (start + dt.timedelta(days=i)) in daily]
    return sum(got), (len(got) / n if n else 0.0)


def season_summary(daily: dict[dt.date, float], zone: str, season_year: int,
                   baseline_years: tuple[int, int] = BASELINE_YEARS) -> dict | None:
    """Season-to-date (or full-season) rainfall vs the same calendar window in the baseline years."""
    if not daily:
        return None
    last = max(daily)
    (sm, sd), (em, ed) = SEASON_WINDOWS[zone]
    start, full_end = dt.date(season_year, sm, sd), dt.date(season_year, em, ed)
    end = min(full_end, last)
    if end < start + dt.timedelta(days=14):
        return None  # season not started (or under two weeks of data): nothing meaningful to report
    total, completeness = _window_total(daily, start, end)
    if completeness < MIN_COMPLETENESS:
        return None
    base_totals = []
    for y in range(baseline_years[0], baseline_years[1] + 1):
        b_start = dt.date(y, sm, sd)
        b_end = dt.date(y, end.month, end.day) if not (end.month == 2 and end.day == 29) else dt.date(y, 2, 28)
        t, comp = _window_total(daily, b_start, b_end)
        if comp >= MIN_COMPLETENESS:
            base_totals.append(t)
    if len(base_totals) < 10:
        return None
    baseline = sum(base_totals) / len(base_totals)
    anomaly = (total / baseline - 1) * 100 if baseline > 0 else 0.0
    complete = end >= full_end
    return {
        "season": str(season_year), "window_start": start.isoformat(), "window_end": end.isoformat(),
        "season_complete": complete, "rainfall_mm": round(total, 1), "seasonal_baseline_mm": round(baseline, 1),
        "rainfall_anomaly_pct": round(anomaly, 2), "drought_indicator": int(anomaly <= DROUGHT_ANOMALY_PCT),
        "baseline_years": f"{baseline_years[0]}-{baseline_years[1]}", "n_baseline_years": len(base_totals),
        "last_data_date": last.isoformat(),
    }
