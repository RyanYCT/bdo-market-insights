"""One-time seed: copy items from bdo.accessory DynamoDB table to bdo-v3-items."""

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


def seed_target_table(items: list[dict], target_table_name: str, *, dry_run: bool) -> None:
    """Write items to the target DynamoDB table."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(target_table_name)
    now = datetime.now(UTC).isoformat()

    for item in items:
        record = {
            "id": int(item["id"]),
            "name": item.get("name", ""),
            "category": item.get("category", ""),
            "main_category": item.get("main_category", ""),
            "sub_category": item.get("sub_category", ""),
            "tracked": "true",
            "created_at": now,
            "updated_at": now,
        }
        if dry_run:
            print(f"[DRY RUN] Would write: {record}")
        else:
            table.put_item(Item=record)
            print(f"Seeded item {record['id']}: {record['name']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed bdo-v3-items DynamoDB table from bdo.accessory"
    )
    parser.add_argument(
        "--source-table",
        default="bdo.accessory",
        help="Source DynamoDB table name (default: bdo.accessory)",
    )
    parser.add_argument(
        "--target-table",
        default="bdo-v3-items",
        help="Target DynamoDB table name (default: bdo-v3-items)",
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
