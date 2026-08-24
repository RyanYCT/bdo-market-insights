"""Build ``scripts/data/full_items.json`` -- the offline market snapshot.

Enumerates arsha.io ``GetWorldMarketList`` across the whole market taxonomy and
writes a flat, de-duplicated list of ``{id, name, main, sub, grade}``: the "full
item list" the track toggle (``select_tracked.py``) and the seed
(``seed_items.py``) read, so those never depend on arsha at run time. This is
the *only* step that calls arsha; re-run it occasionally (e.g. after a BDO patch
adds items).

``GetWorldMarketList`` carries the category codes but not the item ``grade``, so
grade is merged in from a single ``util/db`` call (grade is language-
independent). An id whose grade can't be resolved is written as ``"grade": null``
so the toggle simply can't grade-filter it. Re-run it occasionally (e.g. after a
BDO patch adds items).

    uv run python scripts/build_market_catalog.py
    uv run python scripts/build_market_catalog.py --region tw --max-main 85
"""

from __future__ import annotations

import argparse
import http.client
import json
import time
from pathlib import Path

_OUT_DEFAULT = Path(__file__).parent / "data" / "full_items.json"

#: The offline builder is not Lambda-bound, so it retries harder than the ETL
#: default (``_UTIL_DB_MAX_ATTEMPTS``) to ride out arsha's bursts before giving up.
_CATALOG_BUILD_ATTEMPTS = 6

#: Circuit breaker: abort the crawl (and fail loud) once more than this many
#: categories have failed -- arsha is down, so there is no point probing on.
_CATALOG_MAX_FAILURES = 10


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enumerate the arsha.io market taxonomy into full_items.json"
    )
    parser.add_argument("--region", default="tw", help="arsha region path segment (default: tw)")
    parser.add_argument(
        "--max-main",
        type=int,
        default=85,
        help="Highest main-category code to probe (default: 85)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.2, help="Seconds between requests (default: 0.2)"
    )
    parser.add_argument(
        "--lang",
        default="en",
        help="util/db language for the grade merge (default: en; grade is language-independent)",
    )
    parser.add_argument(
        "--out", type=Path, default=_OUT_DEFAULT, help=f"Output file (default: {_OUT_DEFAULT})"
    )
    args = parser.parse_args()

    from bdo_common.arsha_client import ArshaClient
    from bdo_common.models import MarketListItem
    from bdo_common.tracking import MarketListFetchError, enumerate_taxonomy

    client = ArshaClient(region=args.region)

    def fetch(main: int, sub: int) -> list[MarketListItem]:
        try:
            items = client.fetch_market_list(main, sub, max_attempts=_CATALOG_BUILD_ATTEMPTS)
        except (OSError, ValueError, http.client.IncompleteRead) as exc:
            # fetch_market_list returns [] only for a real 404 boundary, so any
            # error raised here (network, timeout, non-list/JSON body, exhausted
            # retries) is a genuine failure. Record it as a failure (not an empty
            # boundary) so enumerate_taxonomy never silently truncates the crawl.
            raise MarketListFetchError(f"{main}:{sub}") from exc
        if items:
            print(f"  {main}:{sub} -> {len(items)} items")
        time.sleep(args.delay)
        return items

    print(
        f"Enumerating arsha.io market taxonomy (region={args.region}, max-main={args.max_main})..."
    )
    catalog, failures = enumerate_taxonomy(
        fetch, max_main=args.max_main, max_failures=_CATALOG_MAX_FAILURES
    )
    print(f"Discovered {len(catalog)} unique marketable items")

    # Fail loud on an incomplete crawl: a transient outage that exhausted retries
    # must not be written as a truncated snapshot (it would silently drop items).
    if failures:
        print(
            f"\nERROR: {len(failures)} market categories could not be fetched after "
            f"{_CATALOG_BUILD_ATTEMPTS} attempts each (arsha unavailable):"
        )
        for main, sub in failures:
            print(f"  {main}:{sub}")
        print("Snapshot NOT written (it would be incomplete). Re-run when arsha is healthy.")
        raise SystemExit(1)

    # Merge grade from util/db (GetWorldMarketList has no grade). One call for
    # the whole catalog; grade is language-independent so --lang only affects
    # names we don't use here.
    print(f"Fetching item grades from util/db (lang={args.lang})...")
    grade_by_id = {
        entry.item_id: entry.grade
        for entry in client.fetch_item_db(args.lang, max_attempts=_CATALOG_BUILD_ATTEMPTS)
    }
    graded = sum(1 for c in catalog if grade_by_id.get(c.item_id) is not None)
    if graded == 0:
        print(
            "\nERROR: util/db returned no grades (arsha unavailable); snapshot NOT "
            "written (grade filtering would break)."
        )
        raise SystemExit(1)
    print(f"Resolved grade for {graded}/{len(catalog)} items")

    payload = [
        {
            "id": c.item_id,
            "name": c.name,
            "main": c.main_category,
            "sub": c.sub_category,
            "grade": grade_by_id.get(c.item_id),
        }
        for c in catalog
    ]
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(payload)} items to {args.out}")


if __name__ == "__main__":
    main()
