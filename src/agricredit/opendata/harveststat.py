"""Official commune-level crop statistics for Benin from HarvestStat Africa.

HarvestStat harmonises FEWS NET's subnational crop statistics; for Benin the figures come from the
Direction de la Statistique Agricole (Ministere de l'Agriculture) at commune (admin-2) level.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from .geo import read_centroids
from .http import get_bytes
from .sources import HARVESTSTAT_BASE, HARVESTSTAT_VERSION

DATA_FILE = f"hvstat_africa_data_{HARVESTSTAT_VERSION}.csv"
BOUNDARY_FILE = f"hvstat_africa_boundary_{HARVESTSTAT_VERSION}.gpkg"
SOURCE_LABEL = f"HarvestStat Africa {HARVESTSTAT_VERSION} (DSA Benin)"
SOURCE_URL = "https://github.com/HarvestStat/HarvestStat-Africa"

# HarvestStat product name -> agent crop name
PRODUCTS = {"Maize": "maize", "Rice": "rice", "Soybean": "soybean", "Cotton": "cotton", "Cashew (unshelled)": "cashew"}


def zone_for(lat: float) -> str:
    """Approximate agro-climatic zone from latitude (south: two rainy seasons; centre and north: one)."""
    if lat < 7.8:
        return "south"
    if lat < 9.5:
        return "centre"
    return "north"


def fetch_communes(cache: Path) -> list[dict]:
    gpkg = cache / BOUNDARY_FILE
    if not gpkg.exists():
        gpkg.write_bytes(get_bytes(HARVESTSTAT_BASE + BOUNDARY_FILE, cache, suffix=".gpkg"))
    rows = read_centroids(gpkg, "BJ")
    out = []
    for i, r in enumerate(rows, start=1):
        out.append({"location_id": f"BEN-LOC-{i:02d}", "fnid": r["fnid"], "name": r["admin_2"], "admin_1": r["admin_1"],
                    "lat": r["lat"], "lon": r["lon"], "zone": zone_for(r["lat"])})
    if len(out) > 99:
        raise ValueError("more than 99 communes would overflow the BEN-LOC-NN identifier format")
    return out


def parse_yields(raw: bytes, country: str = "Benin") -> list[dict]:
    out = []
    for r in csv.DictReader(io.StringIO(raw.decode("utf-8"))):
        if r["country"] != country or r["product"] not in PRODUCTS:
            continue
        if r["qc_flag"] not in ("0", "0.0"):  # drop values HarvestStat flags as outliers or low-variance
            continue
        try:
            y, area = float(r["yield"]), float(r["area"])
        except ValueError:
            continue
        if not (y > 0 and area > 0):
            continue
        out.append({"fnid": r["fnid"], "crop": PRODUCTS[r["product"]], "harvest_year": int(float(r["harvest_year"])),
                    "season_name": r["season_name"], "area_ha": round(area, 1),
                    "production_t": round(float(r["production"]), 1), "yield_kg_per_ha": round(y * 1000, 1),
                    "source_dataset": SOURCE_LABEL, "source_url": SOURCE_URL})
    out.sort(key=lambda d: (d["fnid"], d["crop"], d["harvest_year"]))
    return out


def fetch_yields(cache: Path) -> list[dict]:
    return parse_yields(get_bytes(HARVESTSTAT_BASE + DATA_FILE, cache, suffix=".csv"))
