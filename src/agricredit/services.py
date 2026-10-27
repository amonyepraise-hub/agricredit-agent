"""Domain services behind the MCP tools. Pure functions over the SQLite store,
so they are unit-testable without an MCP transport. The MCP server wraps them."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import statistics

import numpy as np

from . import db
from .config import AS_OF, get_settings
from .integrity import signed
from .models.schemas import (
    CurrentObligation, DataFreshness, EvidenceItem, EvidenceKind, HouseholdCase, MarketRequest,
    MarketSignal, Plot, RepaymentRecord, WeatherRequest, WeatherSignal, YieldRecord,
)

# Freshness limits come from settings (env-configurable); defaults 30 / 14 / 7 days.
def _repayment_fresh() -> float:
    return get_settings().repayment_freshness_days


def _market_fresh() -> float:
    return get_settings().market_freshness_days


def _weather_fresh() -> float:
    return get_settings().weather_freshness_days

MARKET_CONFLICT_SPREAD = 0.10  # same-day observations disagreeing by >10% are a conflict

# Patterns that indicate an attempt to steer the agent from inside a data field.
INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) instructions",
    r"system (override|prompt)",
    r"\byou (must|should) (respond|output|answer|call)",
    r"approved credit|disburse credit|loan accepted",
    r"release_advisory|call (the )?tool",
    r"do not (mention|tell|reveal)",
    r"</?(system|assistant|instructions?)>",
]


class ToolUnavailableError(RuntimeError):
    """Raised when an upstream data source is unavailable (real or fault-injected)."""


class NotFoundError(LookupError):
    pass


def _now() -> dt.datetime:
    return AS_OF


def _age_days(ts: dt.datetime) -> float:
    return round((_now() - ts).total_seconds() / 86400.0, 2)


def _ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s)


def faults() -> set[str]:
    return {f.strip() for f in get_settings().faults.split(",") if f.strip()}


def _has_table(c, name: str) -> bool:
    return c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def screen_for_injection(text: str | None) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(re.search(p, low) for p in INJECTION_PATTERNS)


def _ev(evid: str, label: str, source_type: str, source_id: str, table: str, key: str, value,
        kind: EvidenceKind, observed: dt.datetime | None, transformation: str | None = None,
        stale_after_days: float | None = None, uri: str | None = None) -> EvidenceItem:
    age = _age_days(observed) if observed else None
    return signed(EvidenceItem(
        evidence_id=evid, label=label, source_type=source_type, source_id=source_id,
        source_uri=uri or db.source_uri(table, key), observation_timestamp=observed,
        retrieval_timestamp=_now(), value=str(value), kind=kind, transformation=transformation,
        is_stale=bool(stale_after_days is not None and age is not None and age > stale_after_days),
        staleness_days=age,
    ))


# --------------------------------------------------------------------------
# Household
# --------------------------------------------------------------------------

def get_household_case(household_id: str) -> HouseholdCase:
    with db.read_conn() as c:
        hh = c.execute("SELECT * FROM households WHERE household_id = ?", (household_id,)).fetchone()
        if hh is None:
            raise NotFoundError(f"household {household_id} not found in the cooperative register")
        plots = [Plot(**dict(r)) for r in c.execute(
            "SELECT plot_id, crop, area_ha, soil_type, location_id FROM plots WHERE household_id = ? ORDER BY plot_id", (household_id,))]
        yields = [YieldRecord(record_id=r["record_id"], season=r["season"], crop=r["crop"], plot_id=r["plot_id"],
                              yield_kg_per_ha=r["yield_kg_per_ha"], recorded_at=_ts(r["recorded_at"]))
                  for r in c.execute("SELECT * FROM yield_history WHERE household_id = ? ORDER BY season, plot_id", (household_id,))]
        repay = [RepaymentRecord(loan_id=r["loan_id"], product=r["product"], amount_fcfa=r["amount_fcfa"],
                                 due_date=dt.date.fromisoformat(r["due_date"]),
                                 paid_date=dt.date.fromisoformat(r["paid_date"]) if r["paid_date"] else None,
                                 days_overdue=r["days_overdue"], defaulted=bool(r["defaulted"]), recorded_at=_ts(r["recorded_at"]))
                 for r in c.execute("SELECT * FROM repayment_history WHERE household_id = ? ORDER BY due_date", (household_id,))]
        obligations = [CurrentObligation(**dict(r)) for r in c.execute(
            "SELECT obligation_id, product, outstanding_amount_fcfa, monthly_due_fcfa FROM current_obligations WHERE household_id = ?", (household_id,))]
        benchmarks = []
        if _has_table(c, "commune_yields") and _has_table(c, "locations"):
            loc = c.execute("SELECT * FROM locations WHERE location_id = ?", (hh["location_id"],)).fetchone()
            crops = sorted({p.crop for p in plots})
            if loc is not None and crops:
                benchmarks = [dict(r) | {"commune": loc["name"]} for r in c.execute(
                    "SELECT * FROM commune_yields WHERE fnid = ? AND crop IN (%s) ORDER BY crop, harvest_year DESC"
                    % ",".join("?" * len(crops)), (loc["fnid"], *crops))]

    latest_repay = max((r.recorded_at for r in repay), default=None)
    rep_age = _age_days(latest_repay) if latest_repay else None
    freshness = DataFreshness(
        repayment_record_age_days=rep_age,
        repayment_is_stale=bool(rep_age is not None and rep_age > _repayment_fresh()),
        repayment_records_missing=len(repay) == 0,
        yield_seasons_observed=len({y.season for y in yields}),
    )

    ev: list[EvidenceItem] = []
    for p in plots:
        ev.append(_ev(f"plot:{p.plot_id}", f"Plot {p.plot_id} ({p.crop}, {p.area_ha} ha)", "household_register",
                      p.plot_id, "plots", p.plot_id, f"{p.crop}, {p.area_ha} ha, {p.location_id}", EvidenceKind.OBSERVED,
                      _ts(hh["created_at"])))
    for y in yields:
        ev.append(_ev(f"yield:{y.record_id}", f"{y.crop} yield {y.season} on {y.plot_id}", "yield_record", str(y.record_id),
                      "yield_history", str(y.record_id), f"{y.yield_kg_per_ha} kg/ha", EvidenceKind.OBSERVED, y.recorded_at))
    for r in repay:
        status = "defaulted" if r.defaulted else (f"paid {r.days_overdue} days late" if r.days_overdue else "paid on time")
        ev.append(_ev(f"repayment:{r.loan_id}", f"Repayment {r.loan_id}", "repayment_record", r.loan_id,
                      "repayment_history", r.loan_id, f"{r.amount_fcfa:,.0f} FCFA due {r.due_date}: {status}",
                      EvidenceKind.OBSERVED, r.recorded_at, stale_after_days=_repayment_fresh()))
    for o in obligations:
        ev.append(_ev(f"obligation:{o.obligation_id}", f"Existing obligation {o.obligation_id}", "obligation_record",
                      str(o.obligation_id), "current_obligations", str(o.obligation_id),
                      f"{o.monthly_due_fcfa:,.0f} FCFA/month ({o.outstanding_amount_fcfa:,.0f} outstanding)", EvidenceKind.OBSERVED, None))

    latest_by_crop: dict[str, dict] = {}
    for b in benchmarks:
        latest_by_crop.setdefault(b["crop"], b)
    for crop, b in latest_by_crop.items():
        ev.append(_ev(f"commune_yield:{b['fnid']}:{crop}:{b['harvest_year']}",
                      f"Official {crop} yield, {b['commune']} commune, {b['harvest_year']}", "official_statistic",
                      f"{b['fnid']}/{crop}/{b['harvest_year']}", "commune_yields", f"{b['fnid']}/{crop}",
                      f"{b['yield_kg_per_ha']:,.0f} kg/ha ({b['area_ha']:,.0f} ha harvested; {b['source_dataset']})",
                      EvidenceKind.OBSERVED, dt.datetime(int(b["harvest_year"]), 12, 31), uri=b["source_url"]))

    return HouseholdCase(
        household_id=household_id, location_id=hh["location_id"], plots=plots, yield_history=yields,
        repayment_history=repay, current_obligations=obligations, data_freshness=freshness,
        notes=hh["notes"], notes_flagged_as_injection=screen_for_injection(hh["notes"]), evidence=ev,
    )


# --------------------------------------------------------------------------
# Weather
# --------------------------------------------------------------------------

def get_weather_signal(req: WeatherRequest) -> WeatherSignal:
    outage = f"weather_unavailable:{req.location_id}" in faults()
    if outage and not req.use_cache_fallback:
        raise ToolUnavailableError(f"upstream rainfall service timed out for {req.location_id} (injected fault)")

    start = dt.datetime.combine(req.start_date, dt.time.min).isoformat()
    end = dt.datetime.combine(req.end_date, dt.time.max).isoformat()
    with db.read_conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM weather_observations WHERE location_id = ? AND crop = ? AND season = ? "
            "AND observation_timestamp BETWEEN ? AND ? ORDER BY observation_timestamp DESC",
            (req.location_id, req.crop, req.season, start, end))]
    if req.use_cache_fallback:
        # Only snapshots retrieved BEFORE the freshness window count as cached copies.
        rows = [r for r in rows if _age_days(_ts(r["retrieval_timestamp"])) > _weather_fresh()] or rows
    if not rows:
        raise NotFoundError(f"no rainfall observation for {req.location_id}/{req.crop}/{req.season} in range")
    r = rows[0]
    obs = _ts(r["observation_timestamp"])
    age = _age_days(obs)
    is_stale = age > _weather_fresh()
    uri = r.get("source_url") or f"dataset://{r['source_dataset']}/{req.location_id}/{req.crop}/{req.season}#obs{r['observation_id']}"
    ev = [
        _ev(f"weather:{r['observation_id']}", f"Rainfall {req.season} season-to-date, {req.location_id}", "rainfall_observation",
            str(r["observation_id"]), "weather_observations", str(r["observation_id"]),
            f"{r['rainfall_mm']} mm ({r['rainfall_anomaly_pct']:+.1f}% vs {r['seasonal_baseline_mm']:.0f} mm baseline)",
            EvidenceKind.OBSERVED, obs, stale_after_days=_weather_fresh(), uri=uri),
        _ev(f"weather_drought:{r['observation_id']}", "Drought indicator", "derived_metric", str(r["observation_id"]),
            "weather_observations", str(r["observation_id"]), "DROUGHT" if r["drought_indicator"] else "no drought",
            EvidenceKind.DERIVED, obs, transformation="drought = rainfall anomaly <= -20% of seasonal baseline",
            stale_after_days=_weather_fresh(), uri=uri),
    ]
    with db.read_conn() as c:
        cross = None
        if _has_table(c, "weather_crosscheck"):
            cross = c.execute("SELECT * FROM weather_crosscheck WHERE location_id = ? AND crop = ? AND season = ? "
                              "ORDER BY observation_timestamp DESC LIMIT 1", (req.location_id, req.crop, req.season)).fetchone()
    if cross is not None:
        gap = cross["rainfall_anomaly_pct"] - r["rainfall_anomaly_pct"]
        ev.append(_ev(f"weather_crosscheck:{cross['crosscheck_id']}", f"Independent rainfall cross-check ({cross['source_dataset']})",
                      "rainfall_observation", str(cross["crosscheck_id"]), "weather_crosscheck", str(cross["crosscheck_id"]),
                      f"{cross['rainfall_mm']} mm ({cross['rainfall_anomaly_pct']:+.1f}% vs its own baseline; "
                      f"{gap:+.1f} points from the primary source)", EvidenceKind.OBSERVED,
                      _ts(cross["observation_timestamp"]), stale_after_days=_weather_fresh(), uri=cross["source_url"]))
    return WeatherSignal(
        location_id=req.location_id, crop=req.crop, season=req.season, rainfall_mm=r["rainfall_mm"],
        rainfall_anomaly_pct=r["rainfall_anomaly_pct"], seasonal_baseline_mm=r["seasonal_baseline_mm"],
        drought_indicator=bool(r["drought_indicator"]), crop_calendar_signal=r["crop_calendar_signal"],
        observation_timestamp=obs, retrieval_timestamp=_now(), source_dataset=r["source_dataset"], age_days=age,
        is_stale=is_stale, from_cache_fallback=req.use_cache_fallback, evidence=ev,
    )


# --------------------------------------------------------------------------
# Market
# --------------------------------------------------------------------------

def get_market_signal(req: MarketRequest) -> MarketSignal:
    start = dt.datetime.combine(req.start_date, dt.time.min).isoformat()
    end = dt.datetime.combine(req.end_date, dt.time.max).isoformat()
    with db.read_conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM market_prices WHERE commodity = ? AND market_centre = ? AND observation_timestamp BETWEEN ? AND ? "
            "ORDER BY observation_timestamp", (req.commodity, req.market_centre, start, end))]
    if not rows:
        raise NotFoundError(f"no {req.commodity} prices for {req.market_centre} in range")

    prices = [r["price_fcfa_per_kg"] for r in rows]
    median = float(statistics.median(prices))
    rets = np.diff(np.log(prices)) if len(prices) > 2 else np.array([0.0])
    vol = float(np.std(rets))

    latest_ts = _ts(rows[-1]["observation_timestamp"])
    latest_day = [r for r in rows if (latest_ts - _ts(r["observation_timestamp"])).total_seconds() < 86400]
    vals = [r["price_fcfa_per_kg"] for r in latest_day]
    conflicting = len({r["source_dataset"] for r in latest_day}) > 1 and (max(vals) - min(vals)) / min(vals) > MARKET_CONFLICT_SPREAD
    age = _age_days(latest_ts)
    stale = age > _market_fresh()
    latest_price = None if conflicting else float(rows[-1]["price_fcfa_per_kg"])

    # Seasonality: compare with the same calendar month in earlier years (needs a multi-year monthly history).
    with db.read_conn() as c:
        history = [dict(r) for r in c.execute(
            "SELECT price_fcfa_per_kg, observation_timestamp FROM market_prices WHERE commodity = ? AND market_centre = ? "
            "AND observation_timestamp < ?", (req.commodity, req.market_centre, f"{latest_ts.year:04d}-01-01"))]
    by_month: dict[int, list[float]] = {}
    years = set()
    for h_ in history:
        t_ = _ts(h_["observation_timestamp"])
        by_month.setdefault(t_.month, []).append(h_["price_fcfa_per_kg"])
        years.add(t_.year)
    monthly = {str(m_): round(float(statistics.median(v)), 2) for m_, v in sorted(by_month.items()) if len(v) >= 2}
    seasonal = monthly.get(str(latest_ts.month))
    seasonal_anom = None if (seasonal is None or latest_price is None) else round((latest_price / seasonal - 1) * 100, 2)
    seasonal_years = f"{min(years)}-{max(years)}" if years else None

    ev = []
    for r in latest_day:
        ev.append(_ev(f"market:{r['price_id']}", f"{req.commodity} price, {req.market_centre}", "market_price_observation",
                      str(r["price_id"]), "market_prices", str(r["price_id"]), f"{r['price_fcfa_per_kg']} FCFA/kg ({r['source_dataset']})",
                      EvidenceKind.OBSERVED, _ts(r["observation_timestamp"]), stale_after_days=_market_fresh(),
                      uri=r.get("source_url") or f"dataset://{r['source_dataset']}/{req.commodity}/{req.market_centre}#price{r['price_id']}"))
    ev.append(_ev(f"market_median:{req.commodity}:{req.market_centre}", f"{req.commodity} {len(rows)}-observation median",
                  "derived_metric", f"{rows[0]['price_id']}..{rows[-1]['price_id']}", "market_prices",
                  f"{req.commodity}/{req.market_centre}", f"{median:.2f} FCFA/kg; volatility {vol:.4f}",
                  EvidenceKind.DERIVED, latest_ts, transformation=f"median and std of weekly log-returns over {req.start_date}..{req.end_date}"))
    if seasonal is not None:
        ev.append(_ev(f"market_seasonal:{req.commodity}:{req.market_centre}:{latest_ts:%m}",
                      f"{req.commodity} price vs the usual {latest_ts:%B} price", "derived_metric",
                      f"{req.commodity}/{req.market_centre}/{latest_ts:%m}", "market_prices", f"{req.commodity}/{req.market_centre}",
                      f"{latest_price} FCFA/kg vs {seasonal:.2f} FCFA/kg median for {latest_ts:%B} {seasonal_years} "
                      f"({seasonal_anom:+.1f}%)" if seasonal_anom is not None else f"{seasonal:.2f} FCFA/kg median for {latest_ts:%B} {seasonal_years}",
                      EvidenceKind.DERIVED, latest_ts,
                      transformation=f"median of {latest_ts:%B} prices in {seasonal_years}, same market and commodity"))

    return MarketSignal(
        commodity=req.commodity, market_centre=req.market_centre, latest_price_fcfa_per_kg=latest_price,
        historical_median_fcfa_per_kg=round(median, 2),
        price_anomaly_pct=None if latest_price is None else round((latest_price / median - 1) * 100, 2),
        volatility=round(vol, 5), observation_timestamp=latest_ts, retrieval_timestamp=_now(),
        source_dataset=rows[-1]["source_dataset"], age_days=age, is_stale=stale, is_conflicting=conflicting,
        conflicting_observations=[{"price_id": r["price_id"], "price": r["price_fcfa_per_kg"], "source": r["source_dataset"]}
                                  for r in latest_day] if conflicting else [],
        n_observations=len(rows), seasonal_median_fcfa_per_kg=seasonal, seasonal_anomaly_pct=seasonal_anom,
        seasonal_years=seasonal_years, monthly_median_fcfa_per_kg=monthly, evidence=ev,
    )


def evidence_version(items: list[EvidenceItem]) -> str:
    """Hash of the evidence set (ids + values + observation timestamps). Changes if any evidence changes."""
    canon = sorted((e.evidence_id, e.value, e.observation_timestamp.isoformat() if e.observation_timestamp else "") for e in items)
    return hashlib.sha256(json.dumps(canon).encode()).hexdigest()[:16]
