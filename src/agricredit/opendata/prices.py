"""Monthly market prices for Benin from WFP (VAM) via the Humanitarian Data Exchange.

The HDX "Benin - Food Prices" dataset is discovered through the CKAN API (package_show), so the
fetcher keeps working when WFP republishes the CSV under a new resource id.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from .http import FetchError, get_bytes, get_json
from .sources import HDX_PACKAGE_SHOW, HDX_WFP_BENIN

SOURCE_LABEL = "WFP VAM via HDX"
DATASET_URL = "https://data.humdata.org/dataset/" + HDX_WFP_BENIN

# Column names differ between WFP export generations; the first alias found wins.
ALIASES = {
    "date": ["date", "mp_year"], "market": ["market", "mktname", "mkt_name"], "commodity": ["commodity", "cmname", "cm_name"],
    "unit": ["unit", "umname", "um_name"], "price": ["price", "mp_price"], "pricetype": ["pricetype", "ptname", "pt_name"],
    "currency": ["currency", "cur_name"], "lat": ["latitude"], "lon": ["longitude"], "admin1": ["admin1", "admname"],
    "admin2": ["admin2"],
}


def crop_for(commodity: str) -> str | None:
    c = commodity.lower()
    if "flour" in c or "meal" in c:
        return None
    if c.startswith("maize") or c.startswith("corn"):
        return "maize"
    if c.startswith("rice"):
        return "rice"
    if "soy" in c:
        return "soybean"
    return None


def kg_divisor(unit: str) -> float | None:
    u = unit.strip().upper().replace(" ", "")
    if u == "KG":
        return 1.0
    m = re.fullmatch(r"(\d+(?:\.\d+)?)KG", u)
    return float(m.group(1)) if m else None


def market_token(name: str) -> str:
    """'Dantokpa (Cotonou)' -> 'Dantokpa Cotonou-Market' (ASCII, safe for the MCP input validator)."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    clean = re.sub(r"[^A-Za-z0-9 _-]+", " ", ascii_name)
    clean = re.sub(r"\s+", " ", clean).strip()[:50] or "Unknown"
    return f"{clean}-Market"


def farmer_variety(crop: str, counts: Counter) -> str | None:
    """The variety a smallholder actually sells. For rice that is paddy (or local milled rice), never imported rice,
    whose shop price says nothing about a Beninese farmer's revenue. Otherwise the most-reported variety."""
    if crop == "rice":
        for key in ("paddy", "local"):
            hits = [(n, v) for v, n in counts.items() if key in v.lower() and "import" not in v.lower()]
            if hits:
                return max(hits)[1]
        return None
    return counts.most_common(1)[0][0]


def find_csv_url(package: dict) -> str:
    if not package.get("success"):
        raise FetchError(f"HDX package_show failed: {str(package)[:300]}")
    resources = package["result"].get("resources", [])
    csvs = [r for r in resources if str(r.get("format", "")).upper() == "CSV" or str(r.get("url", "")).lower().endswith(".csv")]
    for r in csvs:
        name = (str(r.get("name", "")) + " " + str(r.get("url", ""))).lower()
        if "wfp_food_prices" in name and "qc" not in name:
            return r["url"]
    if csvs:
        return csvs[0]["url"]
    raise FetchError("no CSV resource in the HDX WFP Benin dataset")


def _col(row: dict, field: str) -> str:
    for a in ALIASES[field]:
        if a in row and row[a] not in (None, ""):
            return row[a]
    return ""


def parse_prices(raw: bytes, since: dt.date, csv_url: str) -> tuple[list[dict], list[dict]]:
    """Returns (price_rows, markets). Keeps retail prices per kg for maize, rice and soybean; for each
    (crop, market) only the most-reported variety is kept, so two varieties never look like a feed conflict."""
    text = raw.decode("utf-8-sig")
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        d = _col(r, "date")
        if not d or d.startswith("#"):
            continue  # HXL hashtag row
        crop = crop_for(_col(r, "commodity"))
        div = kg_divisor(_col(r, "unit"))
        if crop is None or div is None:
            continue
        try:
            date = dt.date.fromisoformat(d[:10])
            price = float(_col(r, "price")) / div
        except ValueError:
            continue
        if date < since or price <= 0:
            continue
        rows.append({"crop": crop, "commodity": _col(r, "commodity"), "pricetype": _col(r, "pricetype") or "Retail",
                     "market": _col(r, "market"), "date": date, "price": price, "currency": _col(r, "currency") or "XOF",
                     "lat": _col(r, "lat"), "lon": _col(r, "lon"), "admin1": _col(r, "admin1"), "admin2": _col(r, "admin2")})
    if not rows:
        raise FetchError("the WFP file contained no usable maize/rice/soybean prices per kg")
    rows = [r for r in rows if r["currency"].upper() in ("XOF", "FCFA", "CFA")] or rows
    retail = [r for r in rows if r["pricetype"].lower() == "retail"]
    rows = retail or rows

    by_key = defaultdict(list)
    for r in rows:
        by_key[(r["crop"], r["market"])].append(r)
    out, markets = [], {}
    for (crop, market), rs in sorted(by_key.items()):
        variety = farmer_variety(crop, Counter(r["commodity"] for r in rs))
        if variety is None:
            continue
        token = market_token(market)
        per_month = {}
        for r in rs:
            if r["commodity"] == variety:
                per_month[r["date"]] = r  # one observation per date
        for date, r in sorted(per_month.items()):
            out.append({"crop": crop, "market_centre": token, "market": market, "date": date.isoformat(),
                        "price_fcfa_per_kg": round(r["price"], 2), "commodity_label": variety, "pricetype": r["pricetype"],
                        "source_dataset": f"{SOURCE_LABEL} ({variety}, {r['pricetype']})", "source_url": csv_url})
        m = markets.setdefault(token, {"market_centre": token, "market": market, "lat": "", "lon": "",
                                       "admin1": rs[0]["admin1"], "admin2": rs[0]["admin2"], "crops": set()})
        m["crops"].add(crop)
        for r in rs:
            if r["lat"] and r["lon"]:
                m["lat"], m["lon"] = r["lat"], r["lon"]
                break
    for m in markets.values():
        m["crops"] = ",".join(sorted(m["crops"]))
    return out, list(markets.values())


def fetch_prices(cache: Path, since: dt.date, refresh: bool = False) -> tuple[list[dict], list[dict]]:
    package = get_json(HDX_PACKAGE_SHOW, cache, {"id": HDX_WFP_BENIN}, refresh=refresh)
    url = find_csv_url(package)
    return parse_prices(get_bytes(url, cache, suffix=".csv", refresh=refresh), since, url)
