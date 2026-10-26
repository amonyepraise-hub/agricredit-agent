"""python -m agricredit.opendata {sources,fetch,build,all}

  sources   print the open data sources and licences (no internet needed)
  fetch     download every source into data/real/ (needs internet)
  build     build data/agricredit_real.db + the real-mode policy from the downloaded extracts (offline)
  all       fetch, then build
"""
from __future__ import annotations

import argparse
import json

from .sources import SOURCES


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m agricredit.opendata")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sources")
    for name in ("fetch", "all"):
        f = sub.add_parser(name)
        f.add_argument("--refresh", action="store_true", help="ignore the download cache")
        f.add_argument("--crosscheck-limit", type=int, default=20, help="communes that get the ERA5 cross-check")
    b = sub.add_parser("build")
    b.add_argument("--households-per-commune", type=int, default=5)
    a = ap.parse_args()

    if a.cmd == "sources":
        for s in SOURCES:
            print(f"- {s.name}\n    publisher: {s.publisher}\n    licence:   {s.licence}\n    feeds:     {s.feeds}\n    url:       {s.url}")
        return
    if a.cmd in ("fetch", "all"):
        from .fetch import fetch_all
        fetch_all(refresh=a.refresh, crosscheck_limit=a.crosscheck_limit)
    if a.cmd in ("build", "all"):
        from .build import build
        stats = build(households_per_commune=getattr(a, "households_per_commune", 5))
        print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
