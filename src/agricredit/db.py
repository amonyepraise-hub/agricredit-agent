"""SQLite access. Read-only connections for evidence; a separate writer for approvals/outbox.
All queries are parameterised; identifiers are validated by Pydantic before reaching here."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import get_settings


def db_path() -> Path:
    return Path(get_settings().db_path)


@contextmanager
def read_conn() -> Iterator[sqlite3.Connection]:
    p = db_path()
    if not p.exists():
        raise FileNotFoundError(f"database not found at {p}; run `make data` first")
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def source_uri(table: str, key: str) -> str:
    return f"sqlite://data/{db_path().name}/{table}/{key}"


def get_case_row(case_id: str) -> dict | None:
    with read_conn() as c:
        r = c.execute("SELECT * FROM credit_cases WHERE case_id = ?", (case_id,)).fetchone()
        return dict(r) if r else None


def list_cases() -> list[dict]:
    with read_conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM credit_cases ORDER BY case_id")]


def get_package(package_id: str) -> dict | None:
    with read_conn() as c:
        r = c.execute("SELECT * FROM input_packages WHERE package_id = ?", (package_id,)).fetchone()
        return dict(r) if r else None


def get_product(product_id: str) -> dict | None:
    with read_conn() as c:
        r = c.execute("SELECT * FROM loan_products WHERE product_id = ?", (product_id,)).fetchone()
        return dict(r) if r else None


def get_officer(officer_id: str) -> dict | None:
    with read_conn() as c:
        r = c.execute("SELECT * FROM officers WHERE officer_id = ? AND active = 1", (officer_id,)).fetchone()
        return dict(r) if r else None


def list_officers() -> list[dict]:
    with read_conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM officers WHERE active = 1 ORDER BY officer_id")]


def data_sources() -> list[dict]:
    """Public sources behind this store (real-open-data mode); empty for the synthetic store."""
    with read_conn() as c:
        if c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'data_sources'").fetchone() is None:
            return []
        return [dict(r) for r in c.execute("SELECT * FROM data_sources ORDER BY source_key")]
