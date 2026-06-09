"""One-time seed: copy items from bdo.accessory DynamoDB table to bdo-<stage>-items."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

import boto3


def scan_source_table(table_name: str) -> list[dict]:
    """Scan all items from the source DynamoDB table."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)
    items: list[dict] = []
    response = table.scan()
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    return items


# Deboreka-series accessories use cron table B; all other accessories
# use table A (domain-model.md). Stored per-item so the future
# accessory_cron_v1 model can select the right cron counts.
DEBOREKA_IDS = {12094, 12276, 11653, 11882}


def seed_target_table(items: list[dict], target_table_name: str, *, dry_run: bool) -> None:
    """Write items to the target DynamoDB table."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(target_table_name)
    now = datetime.now(UTC).isoformat()

    for item in items:
        # The legacy bdo.accessory table stores metadata under camelCase
        # keys (mainCategory, subCategory); map them to the new snake_case
        # schema. Omit empty values so we never write an empty string into
        # the `category` GSI partition key (DynamoDB rejects that).
        record: dict[str, object] = {
            "id": int(item["id"]),
            "name": item.get("name", ""),
            "tracked": "true",
            "cron_table": "b" if int(item["id"]) in DEBOREKA_IDS else "a",
            "created_at": now,
            "updated_at": now,
        }
        if item.get("category"):
            record["category"] = item["category"]
        if item.get("mainCategory"):
            record["main_category"] = item["mainCategory"]
        if item.get("subCategory"):
            record["sub_category"] = item["subCategory"]

        if dry_run:
            print(f"[DRY RUN] Would write: {record}")
        else:
            table.put_item(Item=record)
            print(f"Seeded item {record['id']}: {record['name']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the per-stage items DynamoDB table from bdo.accessory"
    )
    parser.add_argument(
        "--source-table",
        default="bdo.accessory",
        help="Source DynamoDB table name (default: bdo.accessory)",
    )
    parser.add_argument(
        "--target-table",
        default="bdo-dev-items",
        help="Target DynamoDB table name (per stage, e.g. bdo-dev-items / bdo-prod-items)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print items without writing to target table",
    )
    args = parser.parse_args()

    print(f"Scanning source table: {args.source_table}")
    items = scan_source_table(args.source_table)
    print(f"Found {len(items)} items")

    if not items:
        print("No items found. Exiting.")
        sys.exit(0)

    seed_target_table(items, args.target_table, dry_run=args.dry_run)
    action = "previewed" if args.dry_run else "seeded"
    print(f"Done. {len(items)} items {action}.")


if __name__ == "__main__":
    main()
