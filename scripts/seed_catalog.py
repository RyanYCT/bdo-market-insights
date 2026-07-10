"""One-time / manual full-catalog backfill from arsha.io util/db.

Fetches ``util/db`` for the given languages and partial-upserts every BDO item
into the per-stage items table (ETL-owned fields preserved, ADR-0018).
Idempotent and safe to re-run; this is also the mechanism for the initial
catalog load. The weekly ``catalogSync`` Lambda runs the same shared logic.

    uv run python scripts/seed_catalog.py --target-table bdo-dev-items
    uv run python scripts/seed_catalog.py --target-table bdo-dev-items --langs en,tw --dry-run
"""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill the full BDO item catalog from arsha.io util/db"
    )
    parser.add_argument(
        "--target-table",
        default="bdo-dev-items",
        help="Target DynamoDB table (per stage, e.g. bdo-dev-items / bdo-prod-items)",
    )
    parser.add_argument(
        "--langs",
        default="en,tw",
        help="Comma-separated util/db languages (default: en,tw; English is canonical)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and merge, printing counts, without writing to the table",
    )
    args = parser.parse_args()

    # dynamo reads DYNAMODB_TABLE at import, so set it before importing the layer.
    os.environ["DYNAMODB_TABLE"] = args.target_table
    from bdo_common import catalog, dynamo
    from bdo_common.arsha_client import DEFAULT_LANG, ArshaClient

    langs = [lang.strip() for lang in args.langs.split(",") if lang.strip()]
    client = ArshaClient()

    print(f"Fetching util/db for languages: {langs}")
    by_lang = {lang: client.fetch_item_db(lang) for lang in langs}
    for lang, entries in by_lang.items():
        print(f"  {lang}: {len(entries)} items")

    merged = catalog.merge_catalog(by_lang, default_lang=DEFAULT_LANG)
    print(f"Merged into {len(merged)} unique items")

    if args.dry_run:
        sample = merged[0] if merged else None
        print(f"[DRY RUN] would upsert {len(merged)} items into {args.target_table}")
        if sample is not None:
            print(f"[DRY RUN] sample: {sample!r}")
        return

    total, new = dynamo.bulk_upsert_catalog_items(merged)
    print(f"Done. Upserted {total} items into {args.target_table} ({new} newly created).")


if __name__ == "__main__":
    main()
