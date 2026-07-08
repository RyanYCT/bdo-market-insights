"""One-time backfill: stamp the sparse tracked-index marker on tracked items.

The ``tracked-index`` GSI (ADR-0018) is keyed on a marker attribute (``t``)
written only on tracked items. Items registered before the marker existed lack
it and would be invisible to the ETL's tracked-item Query, so this sets
``t = "1"`` on every item currently ``tracked = "true"``.

Run once, right after deploying the change that adds the GSI, before relying on
the ETL's tracked query. Idempotent and safe to re-run.

    uv run python scripts/backfill_tracked_marker.py --target-table bdo-dev-items
    uv run python scripts/backfill_tracked_marker.py --target-table bdo-dev-items --dry-run
"""

from __future__ import annotations

import argparse

import boto3
from boto3.dynamodb.conditions import Attr

_MARKER_ATTR = "t"
_MARKER_VALUE = "1"


def scan_tracked(table_name: str) -> list[dict]:
    """Scan the target table for all items where tracked=true."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)
    items: list[dict] = []
    kwargs: dict = {"FilterExpression": Attr("tracked").eq("true")}
    response = table.scan(**kwargs)
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
    return items


def backfill(table_name: str, *, dry_run: bool) -> tuple[int, int]:
    """Set the marker on tracked items missing it. Returns (tracked, written)."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)
    tracked = scan_tracked(table_name)
    written = 0

    for item in tracked:
        item_id = int(item["id"])
        if item.get(_MARKER_ATTR) == _MARKER_VALUE:
            print(f"[skip] {item_id} already marked")
            continue
        if dry_run:
            print(f"[DRY RUN] would set {_MARKER_ATTR}={_MARKER_VALUE!r} on {item_id}")
        else:
            table.update_item(
                Key={"id": item_id},
                UpdateExpression="SET #t = :v",
                ExpressionAttributeNames={"#t": _MARKER_ATTR},
                ExpressionAttributeValues={":v": _MARKER_VALUE},
            )
            print(f"marked {item_id}")
        written += 1

    return len(tracked), written


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill the sparse tracked-index marker on tracked items"
    )
    parser.add_argument(
        "--target-table",
        default="bdo-dev-items",
        help="Target DynamoDB table name (per stage, e.g. bdo-dev-items / bdo-prod-items)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned writes without modifying the table",
    )
    args = parser.parse_args()

    print(f"Scanning {args.target_table} for tracked items...")
    total, written = backfill(args.target_table, dry_run=args.dry_run)
    action = "would mark" if args.dry_run else "marked"
    print(f"Done. {total} tracked items found; {action} {written}.")


if __name__ == "__main__":
    main()
