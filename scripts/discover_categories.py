"""Discover the BDO market taxonomy by probing arsha.io GetWorldMarketList.

Enumerates every (mainCategory, subCategory) arsha.io serves, with item counts
and sample names. Use it to find category codes when extending
``scripts/data/categories.json`` (e.g. locating a new item group, or where a
specific item lives).

arsha returns HTTP 404 for a nonexistent combination, which is the stop signal:

* main categories are probed as 1, then multiples of 5 (1, 5, 10, ..., --max-main)
* for each main, sub categories run 1, 2, 3, ... until the first 404

Transient failures (HTTP 5xx / 429 / timeout) are retried with linear backoff,
since ``util/db`` and the market endpoints intermittently return 500.

    uv run python scripts/discover_categories.py
    uv run python scripts/discover_categories.py --out taxonomy.json
    uv run python scripts/discover_categories.py --find 17081,47688,601204
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_BASE_URL = "https://api.arsha.io/v2"
_MAX_ATTEMPTS = 4
_TIMEOUT_SECONDS = 30
_BACKOFF_SECONDS = 3.0


def main_category_codes(max_main: int) -> list[int]:
    """Candidate main-category codes: 1, then 5, 10, ..., ``max_main``."""
    return [1, *range(5, max_main + 1, 5)]


def fetch_category(base_url: str, region: str, main: int, sub: int) -> list[dict[str, Any]] | None:
    """Return the item rows for one (main, sub), or ``None`` if arsha 404s it.

    A 404 means the combination does not exist (the enumeration boundary).
    Transient errors (5xx / 429 / timeout) are retried with linear backoff.
    """
    url = f"{base_url}/{region}/GetWorldMarketList?mainCategory={main}&subCategory={sub}&lang=en"
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            # URL is built from ints/known strings and is always https://api.arsha.io/...
            with urllib.request.urlopen(  # noqa: S310  # nosec B310
                url, timeout=_TIMEOUT_SECONDS
            ) as resp:
                data = json.loads(resp.read().decode())
                return data if isinstance(data, list) else []
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            retryable = exc.code == 429 or exc.code >= 500
            if not retryable or attempt == _MAX_ATTEMPTS:
                raise
            time.sleep(_BACKOFF_SECONDS * attempt)
        except (urllib.error.URLError, TimeoutError):
            if attempt == _MAX_ATTEMPTS:
                raise
            time.sleep(_BACKOFF_SECONDS * attempt)
    return None  # unreachable


def discover(
    *, base_url: str, region: str, max_main: int, max_sub: int, delay: float
) -> dict[str, list[dict[str, Any]]]:
    """Probe every (main, sub) and return ``{"main:sub": [item rows]}``."""
    taxonomy: dict[str, list[dict[str, Any]]] = {}
    for main in main_category_codes(max_main):
        sub = 1
        while sub <= max_sub:
            items = fetch_category(base_url, region, main, sub)
            if items is None:  # 404 -> no (more) subs for this main
                break
            taxonomy[f"{main}:{sub}"] = items
            print(f"  found {main}:{sub} ({len(items)} items)")
            sub += 1
            time.sleep(delay)
    return taxonomy


def _report(taxonomy: dict[str, list[dict[str, Any]]], find_ids: list[int]) -> None:
    """Print a per-category summary and, optionally, locate specific item ids."""
    print("\n=== taxonomy ===")
    total_items = 0
    for key, items in taxonomy.items():
        total_items += len(items)
        sample = ", ".join(str(i.get("name", "?")) for i in items[:3])
        print(f"{key:>8}  {len(items):>4} items  e.g. {sample}")
    print(f"\n{len(taxonomy)} categories, {total_items} item rows total")

    if find_ids:
        index: dict[int, tuple[str, str]] = {}
        for key, items in taxonomy.items():
            for it in items:
                index[int(it["id"])] = (key, str(it.get("name", "")))
        print("\n=== located ids (main:sub) ===")
        for fid in find_ids:
            hit = index.get(fid)
            print(f"{fid:>8}: {hit[0]}  ({hit[1]})" if hit else f"{fid:>8}: NOT FOUND")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--region", default="tw", help="arsha region path segment (default: tw)")
    parser.add_argument(
        "--max-main",
        type=int,
        default=85,
        help="Highest main-category code to probe (default: 85)",
    )
    parser.add_argument(
        "--max-sub", type=int, default=30, help="Safety cap on sub codes per main (default: 30)"
    )
    parser.add_argument(
        "--delay", type=float, default=0.2, help="Seconds to wait between requests (default: 0.2)"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write the full taxonomy (id/name/codes) to this JSON file",
    )
    parser.add_argument(
        "--find", default="", help="Comma-separated item ids to locate in the taxonomy"
    )
    args = parser.parse_args()

    find_ids = [int(x) for x in args.find.split(",") if x.strip()]

    print(
        f"Probing arsha.io ({args.region}) main categories {main_category_codes(args.max_main)}..."
    )
    taxonomy = discover(
        base_url=_BASE_URL,
        region=args.region,
        max_main=args.max_main,
        max_sub=args.max_sub,
        delay=args.delay,
    )
    _report(taxonomy, find_ids)

    if args.out is not None:
        dump = {
            key: [
                {
                    "id": int(i["id"]),
                    "name": i.get("name", ""),
                    "mainCategory": i.get("mainCategory"),
                    "subCategory": i.get("subCategory"),
                }
                for i in items
            ]
            for key, items in taxonomy.items()
        }
        args.out.write_text(
            json.dumps(dump, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"\nWrote full taxonomy to {args.out}")


if __name__ == "__main__":
    main()
