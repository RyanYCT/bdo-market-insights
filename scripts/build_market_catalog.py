"""Build ``scripts/data/full_items.json`` -- the offline market snapshot.

Enumerates arsha.io ``GetWorldMarketList`` across the whole market taxonomy and
writes a flat, de-duplicated list of ``{id, name, main, sub}``: the "full item
list" the track toggle (``select_tracked.py``) and the seed (``seed_items.py``)
read, so those never depend on arsha at run time. This is the *only* step that
calls arsha; re-run it occasionally (e.g. after a BDO patch adds items).

    uv run python scripts/build_market_catalog.py
    uv run python scripts/build_market_catalog.py --region tw --max-main 85
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

_OUT_DEFAULT = Path(__file__).parent / "data" / "full_items.json"


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
        "--out", type=Path, default=_OUT_DEFAULT, help=f"Output file (default: {_OUT_DEFAULT})"
    )
    args = parser.parse_args()

    from bdo_common.arsha_client import ArshaClient
    from bdo_common.tracking import enumerate_taxonomy

    client = ArshaClient(region=args.region)

    def fetch(main: int, sub: int) -> list:  # type: ignore[type-arg]
        items = client.fetch_market_list(main, sub)
        if items:
            print(f"  {main}:{sub} -> {len(items)} items")
        time.sleep(args.delay)
        return items

    print(
        f"Enumerating arsha.io market taxonomy (region={args.region}, max-main={args.max_main})..."
    )
    catalog = enumerate_taxonomy(fetch, max_main=args.max_main)
    print(f"Discovered {len(catalog)} unique marketable items")

    payload = [
        {"id": c.item_id, "name": c.name, "main": c.main_category, "sub": c.sub_category}
        for c in catalog
    ]
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(payload)} items to {args.out}")


if __name__ == "__main__":
    main()
