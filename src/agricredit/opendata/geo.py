"""Minimal GeoPackage reader: commune polygons -> centroids, with no GIS dependencies.

A GeoPackage is an SQLite file whose geometry column holds a small header followed by
standard WKB. Only (Multi)Polygons in WGS84 are needed here.
"""
from __future__ import annotations

import math
import sqlite3
import struct
from pathlib import Path


def _parse_wkb(buf: bytes, off: int = 0):
    """Return (list_of_polygons, new_offset); each polygon is a list of rings of (x, y)."""
    order = "<" if buf[off] == 1 else ">"
    gtype = struct.unpack_from(order + "I", buf, off + 1)[0]
    off += 5
    has_z = gtype in (1003, 1006) or bool(gtype & 0x80000000)
    base = gtype % 1000 if gtype < 0x80000000 else gtype & 0xFF
    dims = 3 if has_z else 2

    def ring(o):
        (n,) = struct.unpack_from(order + "I", buf, o)
        o += 4
        pts = []
        for _ in range(n):
            x, y = struct.unpack_from(order + "dd", buf, o)
            pts.append((x, y))
            o += 8 * dims
        return pts, o

    if base == 3:  # Polygon
        (nr,) = struct.unpack_from(order + "I", buf, off)
        off += 4
        rings = []
        for _ in range(nr):
            r, off = ring(off)
            rings.append(r)
        return [rings], off
    if base == 6:  # MultiPolygon
        (np_,) = struct.unpack_from(order + "I", buf, off)
        off += 4
        polys = []
        for _ in range(np_):
            p, off = _parse_wkb(buf, off)
            polys.extend(p)
        return polys, off
    raise ValueError(f"unsupported WKB geometry type {gtype}")


def gpkg_geometry(blob: bytes):
    if blob[:2] != b"GP":
        raise ValueError("not a GeoPackage geometry blob")
    flags = blob[3]
    env = (flags >> 1) & 0x07
    env_len = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[env]
    polys, _ = _parse_wkb(blob, 8 + env_len)
    return polys


def centroid(polys) -> tuple[float, float]:
    """Area-weighted centroid of the outer rings (holes ignored; fine for commune centroids). Returns (lat, lon)."""
    a_tot = cx = cy = 0.0
    for rings in polys:
        pts = rings[0]
        a = x_acc = y_acc = 0.0
        for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
            cross = x0 * y1 - x1 * y0
            a += cross
            x_acc += (x0 + x1) * cross
            y_acc += (y0 + y1) * cross
        if abs(a) < 1e-15:
            continue
        a *= 0.5
        cx += x_acc / 6.0
        cy += y_acc / 6.0
        a_tot += a
    if abs(a_tot) < 1e-15:
        raise ValueError("degenerate polygon")
    return round(cy / a_tot, 5), round(cx / a_tot, 5)


def read_centroids(gpkg: Path, fnid_prefix: str) -> list[dict]:
    c = sqlite3.connect(gpkg)
    try:
        table = c.execute("SELECT table_name FROM gpkg_contents WHERE data_type = 'features'").fetchone()[0]
        geom_col = c.execute("SELECT column_name FROM gpkg_geometry_columns WHERE table_name = ?", (table,)).fetchone()[0]
        rows = c.execute(f'SELECT fnid, admin_1, admin_2, "{geom_col}" FROM "{table}" WHERE fnid LIKE ? ORDER BY fnid',
                         (fnid_prefix + "%",)).fetchall()
    finally:
        c.close()
    out = []
    for fnid, a1, a2, blob in rows:
        lat, lon = centroid(gpkg_geometry(blob))
        out.append({"fnid": fnid, "admin_1": a1, "admin_2": a2, "lat": lat, "lon": lon})
    return out


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))
