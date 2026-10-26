"""Polite, cached HTTP downloads for the open-data fetchers.

Every download is written to data/real/cache/ first, so a second run (or a run without internet)
reuses it, and the exact bytes the agent's evidence came from stay on disk for inspection.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import httpx

USER_AGENT = "AgriCredit-Agent/0.2 (open-data fetcher; https://github.com/amonyepraise-hub/agricredit-agent)"


class FetchError(RuntimeError):
    pass


def cache_name(url: str, params: dict | None = None, suffix: str = "") -> str:
    key = url + ("?" + json.dumps(params, sort_keys=True) if params else "")
    return hashlib.sha256(key.encode()).hexdigest()[:20] + suffix


def get_bytes(url: str, cache_dir: Path, params: dict | None = None, suffix: str = "", timeout: float = 180.0,
              retries: int = 3, refresh: bool = False, pause_s: float = 1.0) -> bytes:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / cache_name(url, params, suffix)
    if path.exists() and not refresh:
        return path.read_bytes()
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = httpx.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True)
            if r.status_code == 429:  # rate limited: wait out the per-minute window, then retry
                time.sleep(65)
                raise FetchError(f"HTTP 429 (rate limited) from {url}")
            if r.status_code >= 500:
                raise FetchError(f"HTTP {r.status_code} from {url}")
            r.raise_for_status()
            path.write_bytes(r.content)
            time.sleep(pause_s)  # be polite to free public services
            return r.content
        except (httpx.HTTPError, FetchError) as e:
            last = e
            time.sleep(min(30, 3 * attempt))
    raise FetchError(f"could not download {url} after {retries} attempts: {last}")


def get_json(url: str, cache_dir: Path, params: dict | None = None, **kw) -> dict:
    raw = get_bytes(url, cache_dir, params, suffix=".json", **kw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise FetchError(f"{url} did not return JSON: {raw[:200]!r}") from e
