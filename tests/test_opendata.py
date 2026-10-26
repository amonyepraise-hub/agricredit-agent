"""Real-open-data mode: parsers for each public source, and an offline end-to-end build.

The payloads below are SMALL HAND-WRITTEN FIXTURES in each service's documented response format.
They exist only to test the parsing and plumbing; they are never written into data/real/.
"""
import datetime as dt
import json
import shutil
import struct

import pytest

from agricredit.config import REPO_ROOT
from agricredit.opendata import geo, harveststat, prices, rainfall
from agricredit.opendata.fetch import write_csv

AS_OF = "2026-10-07T09:00:00"  # same clock as the synthetic store, so in-process and MCP-server clocks agree


# --------------------------------------------------------------------------- parsers

def test_parse_power_drops_fill_values():
    payload = {"header": {"fill_value": -999.0},
               "properties": {"parameter": {"PRECTOTCORR": {"20250601": 3.2, "20250602": -999.0, "20250603": 0.0}}}}
    out = rainfall.parse_power(payload)
    assert out == {dt.date(2025, 6, 1): 3.2, dt.date(2025, 6, 3): 0.0}


def test_parse_power_rejects_unexpected_shape():
    with pytest.raises(rainfall.FetchError):
        rainfall.parse_power({"messages": ["rate limited"]})


def test_parse_openmeteo():
    payload = {"daily": {"time": ["2025-06-01", "2025-06-02"], "precipitation_sum": [1.5, None]}}
    assert rainfall.parse_openmeteo(payload) == {dt.date(2025, 6, 1): 1.5}


def _daily(base_mm: float, current_mm: float, current_year: int, last: dt.date) -> dict:
    d, out = dt.date(1991, 1, 1), {}
    while d <= last:
        out[d] = current_mm if d.year == current_year else base_mm
        d += dt.timedelta(days=1)
    return out


def test_season_summary_flags_drought_against_baseline():
    s = rainfall.season_summary(_daily(5.0, 3.0, 2026, dt.date(2026, 10, 5)), "south", 2026)
    assert s["season_complete"] and s["drought_indicator"] == 1
    assert s["rainfall_anomaly_pct"] == pytest.approx(-40.0)
    assert s["window_start"] == "2026-03-15" and s["window_end"] == "2026-07-31"


def test_season_summary_season_to_date_uses_same_window_in_baseline():
    s = rainfall.season_summary(_daily(4.0, 4.0, 2026, dt.date(2026, 7, 1)), "north", 2026)
    assert not s["season_complete"] and s["window_end"] == "2026-07-01"
    assert s["rainfall_anomaly_pct"] == pytest.approx(0.0) and s["drought_indicator"] == 0


def test_season_summary_none_before_season_starts():
    assert rainfall.season_summary(_daily(4.0, 4.0, 2026, dt.date(2026, 5, 20)), "north", 2026) is None


WFP_CSV = """date,admin1,admin2,market,market_id,latitude,longitude,category,commodity,commodity_id,unit,priceflag,pricetype,currency,price,usdprice
#date,#adm1+name,#adm2+name,#loc+market+name,#loc+market+code,#geo+lat,#geo+lon,#item+type,#item+name,#item+code,#item+unit,#item+price+flag,#item+price+type,#currency,#value,#value+usd
2026-07-15,Zou,Bohicon,Bohicon,1,7.18,2.07,cereals and tubers,Maize (white),2,KG,actual,Retail,XOF,210,0.35
2026-08-15,Zou,Bohicon,Bohicon,1,7.18,2.07,cereals and tubers,Maize (white),2,KG,actual,Retail,XOF,190,0.32
2026-08-15,Zou,Bohicon,Bohicon,1,7.18,2.07,cereals and tubers,Maize (yellow),3,KG,actual,Retail,XOF,400,0.66
2026-08-15,Zou,Bohicon,Bohicon,1,7.18,2.07,cereals and tubers,Maize (white),2,100 KG,actual,Wholesale,XOF,15000,25
2026-08-15,Borgou,Parakou,Parakou,2,9.35,2.62,cereals and tubers,Rice (imported),5,50 KG,actual,Retail,XOF,25000,41
2026-08-15,Borgou,Parakou,Parakou,2,9.35,2.62,cereals and tubers,Maize flour,6,KG,actual,Retail,XOF,500,0.8
2016-08-15,Borgou,Parakou,Parakou,2,9.35,2.62,cereals and tubers,Rice (imported),5,KG,actual,Retail,XOF,350,0.6
"""


def test_parse_wfp_prices_keeps_one_variety_per_market_in_fcfa_per_kg():
    rows, markets = prices.parse_prices(WFP_CSV.encode(), dt.date(2021, 1, 1), "https://example.org/wfp.csv")
    maize = [r for r in rows if r["crop"] == "maize"]
    assert [r["price_fcfa_per_kg"] for r in maize] == [210.0, 190.0]  # white maize retail only, yellow + wholesale dropped
    rice = [r for r in rows if r["crop"] == "rice"]
    assert len(rice) == 1 and rice[0]["price_fcfa_per_kg"] == 500.0  # 25,000 per 50 kg; the 2016 row is too old
    assert {m["market_centre"] for m in markets} == {"Bohicon-Market", "Parakou-Market"}
    assert all(r["source_url"] == "https://example.org/wfp.csv" for r in rows)


def test_find_hdx_csv_url_skips_quickcharts_file():
    pkg = {"success": True, "result": {"resources": [
        {"name": "wfp_food_prices_ben_qc.csv", "format": "CSV", "url": "https://x/qc.csv"},
        {"name": "wfp_food_prices_ben.csv", "format": "CSV", "url": "https://x/main.csv"}]}}
    assert prices.find_csv_url(pkg) == "https://x/main.csv"


def test_market_token_is_ascii_and_valid():
    t = prices.market_token("Glazoué (Dantokpa) / Nord")
    assert t == "Glazoue Dantokpa Nord-Market"


def _gp_square(x0, y0, size):
    ring = [(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size), (x0, y0)]
    wkb = struct.pack("<BII", 1, 3, 1) + struct.pack("<I", len(ring)) + b"".join(struct.pack("<dd", *p) for p in ring)
    return b"GP\x00\x01" + struct.pack("<i", 4326) + wkb  # flags 0x01: little endian, no envelope


def test_geopackage_centroid():
    lat, lon = geo.centroid(geo.gpkg_geometry(_gp_square(2.0, 9.0, 1.0)))
    assert (lat, lon) == (9.5, 2.5)


HS_CSV = """fnid,country,country_code,admin_1,admin_2,product,season_name,planting_year,planting_month,harvest_year,harvest_month,crop_production_system,qc_flag,area,production,yield
BJ1,Benin,BJ,Zou,Bohicon,Maize,Main,2023,3,2023,10,All (PS),0,1000.0,1200.0,1.2
BJ1,Benin,BJ,Zou,Bohicon,Maize,Main,2022,3,2022,10,All (PS),1,1000.0,9000.0,9.0
BJ1,Benin,BJ,Zou,Bohicon,Tomato,Main,2023,3,2023,10,All (PS),0,10.0,100.0,10.0
TG1,Togo,TG,X,Y,Maize,Main,2023,3,2023,10,All (PS),0,1.0,1.0,1.0
"""


def test_harveststat_keeps_benin_target_crops_without_qc_flags():
    out = harveststat.parse_yields(HS_CSV.encode())
    assert len(out) == 1 and out[0]["yield_kg_per_ha"] == 1200.0 and out[0]["crop"] == "maize"


# --------------------------------------------------------------------------- offline end-to-end build

COMMUNES = [  # name, admin_1, lat, lon (approximate real centroids)
    ("Bohicon", "Zou", 7.17, 2.11), ("Glazoue", "Collines", 8.17, 2.23), ("Parakou", "Borgou", 9.35, 2.62),
    ("Kandi", "Alibori", 11.22, 2.98), ("Malanville", "Alibori", 11.67, 3.27), ("Djougou", "Donga", 9.70, 1.67),
]


@pytest.fixture(scope="module")
def real_store(tmp_path_factory):
    from agricredit.opendata.build import build

    tmp = tmp_path_factory.mktemp("real")
    proc = tmp / "processed"
    communes = [{"location_id": f"BEN-LOC-{i:02d}", "fnid": f"BJT{i}", "name": n, "admin_1": a, "lat": la, "lon": lo,
                 "zone": harveststat.zone_for(la)} for i, (n, a, la, lo) in enumerate(COMMUNES, start=1)]
    write_csv(proc / "communes.csv", communes)
    yields = [{"fnid": c["fnid"], "crop": crop, "harvest_year": y, "season_name": "Main", "area_ha": 5000.0,
               "production_t": 5000.0 * base, "yield_kg_per_ha": base * 1000 * (1 + 0.03 * (y - 2022)),
               "source_dataset": harveststat.SOURCE_LABEL, "source_url": harveststat.SOURCE_URL}
              for c in communes for crop, base in (("maize", 1.2), ("rice", 2.4)) for y in (2021, 2022, 2023)]
    write_csv(proc / "commune_yields.csv", yields)
    last = dt.date(2026, 10, 5)
    primary, cross = [], []
    for i, c in enumerate(communes):
        cur = 5.0 * (0.7 + 0.1 * i)  # anomalies from -30% to +20%
        s = rainfall.season_summary(_daily(5.0, cur, 2026, last), c["zone"], 2026)
        primary.append({"location_id": c["location_id"], "source": "primary", "source_dataset": rainfall.ERA5_SOURCE,
                        "source_url": rainfall.openmeteo_url(c["lat"], c["lon"], dt.date(2026, 3, 15), last)} | s)
        cross.append({"location_id": c["location_id"], "source": "crosscheck", "source_dataset": rainfall.POWER_SOURCE,
                      "source_url": rainfall.power_url(c["lat"], c["lon"], dt.date(1991, 1, 1), last)} | s)
    write_csv(proc / "rainfall_seasons.csv", primary)
    write_csv(proc / "rainfall_crosscheck.csv", cross)
    lines = WFP_CSV.splitlines()[:2]
    for mk, la, lo in (("Bohicon", 7.18, 2.07), ("Parakou", 9.35, 2.62), ("Malanville", 11.67, 3.27)):
        for k, month in enumerate(range(1, 10)):
            lines.append(f"2026-{month:02d}-15,A,B,{mk},1,{la},{lo},c,Maize (white),2,KG,actual,Retail,XOF,{200 + 5 * k},0.3")
            lines.append(f"2026-{month:02d}-15,A,B,{mk},1,{la},{lo},c,Rice (imported),5,KG,actual,Retail,XOF,{450 + 3 * k},0.7")
    rows, markets = prices.parse_prices("\n".join(lines).encode(), dt.date(2021, 1, 1), "https://data.humdata.org/x.csv")
    write_csv(proc / "market_prices.csv", rows)
    write_csv(proc / "markets.csv", markets)
    (proc / "snapshot.json").write_text(json.dumps({"as_of": AS_OF, "current_season": "2026", "sources": {}, "warnings": []}))

    fs_root = tmp / "fs_root"
    shutil.copytree(REPO_ROOT / "fs_mcp_root" / "policy", fs_root / "policy")
    stats = build(households_per_commune=25, db_path=tmp / "real.db", processed=proc,
                  policy_out=fs_root / "policy" / "agent_policy_real.json", sources_md=tmp / "SOURCES.md")
    return tmp, fs_root, stats


def test_build_creates_showcase_cases_and_policy(real_store):
    tmp, fs_root, stats = real_store
    assert stats["cases"] >= 4 and stats["households"] == 150
    assert any("driest commune" in s for s in stats["showcase"])
    pol = json.loads((fs_root / "policy" / "agent_policy_real.json").read_text())
    assert pol["data_mode"] == "real-open-data"
    assert set(pol["market"]["alternate_centres"]) == {"Bohicon-Market", "Parakou-Market", "Malanville-Market"}
    assert "simulated" in (tmp / "SOURCES.md").read_text().lower()


def test_agent_runs_on_real_mode_store_with_cited_sources(real_store, monkeypatch):
    tmp, fs_root, stats = real_store
    monkeypatch.setenv("AGRICREDIT_DB_PATH", str(tmp / "real.db"))
    monkeypatch.setenv("AGRICREDIT_FS_MCP_ROOT", str(fs_root))
    monkeypatch.setenv("AGRICREDIT_POLICY_FILE", "agent_policy_real.json")
    monkeypatch.setenv("AGRICREDIT_RISK_MODEL_PATH", str(tmp / "risk_model_real.joblib"))
    monkeypatch.setenv("AGRICREDIT_MARKET_FRESHNESS_DAYS", "90")
    monkeypatch.setenv("AGRICREDIT_WEATHER_FRESHNESS_DAYS", "14")
    monkeypatch.setenv("AGRICREDIT_TARGET_DEFAULT_RATE", "0.094")
    from agricredit.risk import train
    train.main()
    card = json.loads((tmp / "risk_model_real_card.json").read_text())
    assert abs(card["default_rate"] - 0.094) < 0.05 and "BCEAO" in card["label_source"]

    from agricredit.agent.runner import AgentRunner
    case_id = stats["showcase"][0].split(":")[0]
    st = AgentRunner().start(case_id)
    assert st["status"] == "AWAITING_APPROVAL", st.get("refusal_reason")
    ev = st["advisory"]["evidence"]
    uris = " ".join(e["source_uri"] for e in ev)
    assert "archive-api.open-meteo.com" in uris and "power.larc.nasa.gov" in uris
    assert "data.humdata.org" in uris and "HarvestStat" in uris
    assert any(e["evidence_id"].startswith("weather_crosscheck:") for e in ev)
    assert any(e["evidence_id"].startswith("commune_yield:") for e in ev)
