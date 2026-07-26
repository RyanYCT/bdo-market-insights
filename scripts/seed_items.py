"""Seed the tracked-item set into bdo-<stage>-items from a curated list.

Reads ``scripts/data/tracked_items.json`` (the items to track) and derives each
item's category from arsha.io ``GetWorldMarketList`` via
``scripts/data/categories.json`` (mainCategory/subCategory -> coarse label).
Writes ``tracked=true`` + the sparse tracked-index marker + ``cron_table`` +
``main_category``/``sub_category``/``category`` as a **partial upsert**, so the
catalog-owned fields (``name``/``grade``/``names``) that ``seed_catalog`` /
``catalogSync`` populate are preserved (ADR-0018).

Category is derived entirely from the live taxonomy via ``categories.json``
(e.g. accessories under ``20:1``-``20:4``, buff consumables under ``55:6``); an
item not covered by that map is tracked but left ungrouped -- extend the map.
The optional ``name`` on each entry is an informational comment (the catalog
owns the authoritative name) and is ignored on write.

    uv run python scripts/seed_items.py --target-table bdo-dev-items
    uv run python scripts/seed_items.py --target-table bdo-dev-items --dry-run
    # Regenerate the list from a running environment's current tracked set:
    uv run python scripts/seed_items.py --target-table bdo-prod-items --export
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bdo_common.arsha_client import ArshaClient

_DATA_DIR = Path(__file__).parent / "data"
_TRACKED_ITEMS_FILE = _DATA_DIR / "tracked_items.json"
_CATEGORIES_FILE = _DATA_DIR / "categories.json"


def _load_json(path: Path) -> Any:
    """Load and parse a JSON file."""
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def build_category_index(
    client: ArshaClient, categories: dict[str, Any]
) -> dict[int, tuple[str, int, int]]:
    """Build ``id -> (category, main_category, sub_category)`` from arsha.

    Fetches ``GetWorldMarketList`` for every ``"main:sub"`` key in the category
    map, so an item's coarse category is derived from the live BDO taxonomy
    rather than hand-entered. Keys starting with ``_`` (e.g. comments) are
    skipped.
    """
    index: dict[int, tuple[str, int, int]] = {}
    for key, spec in categories.items():
        if key.startswith("_"):
            continue
        main_str, _, sub_str = key.partition(":")
        main_int, sub_int = int(main_str), int(sub_str)
        category = str(spec["category"])
        for entry in client.fetch_market_list(main_int, sub_int):
            index[entry.item_id] = (category, entry.main_category, entry.sub_category)
    return index


def build_item_updates(
    entry: dict[str, Any], index: dict[int, tuple[str, int, int]]
) -> tuple[dict[str, Any], bool]:
    """Build the DynamoDB partial-update for one tracked entry.

    Returns ``(updates, classified)``. ``category``/``main``/``sub`` are derived
    entirely from the arsha taxonomy index; ``classified`` is False when the item
    is not found there (so it is tracked but ungrouped -- add its category to
    categories.json).
    """
    updates: dict[str, Any] = {"tracked": "true", "cron_table": entry.get("cron_table", "a")}
    if "model_id" in entry:
        updates["model_id"] = entry["model_id"]

    found = index.get(int(entry["id"]))
    if found is not None:
        category, main, sub = found
        updates["category"] = category
        updates["main_category"] = str(main)
        updates["sub_category"] = str(sub)
        return updates, True
    return updates, False


def _export(dynamo: Any, path: Path) -> None:
    """Write the current tracked set from the table to the items file."""
    items = dynamo.list_tracked_items()
    records: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda i: i.id):
        rec: dict[str, Any] = {"id": item.id, "name": item.name}
        if item.cron_table != "a":
            rec["cron_table"] = item.cron_table
        if item.model_id != "accessory_v1":
            rec["model_id"] = item.model_id
        records.append(rec)
    path.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Exported {len(records)} tracked items to {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the tracked-item set into the per-stage items table"
    )
    parser.add_argument(
        "--target-table",
        default="bdo-dev-items",
        help="Target DynamoDB table (per stage, e.g. bdo-dev-items / bdo-prod-items)",
    )
    parser.add_argument(
        "--items-file",
        type=Path,
        default=_TRACKED_ITEMS_FILE,
        help="Curated list of items to track (default: scripts/data/tracked_items.json)",
    )
    parser.add_argument(
        "--categories-file",
        type=Path,
        default=_CATEGORIES_FILE,
        help="Taxonomy map for category derivation (default: scripts/data/categories.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the derived updates without writing to the table",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Write the current tracked set from --target-table to --items-file, then exit",
    )
    args = parser.parse_args()

    # dynamo reads DYNAMODB_TABLE at import, so set it before importing the layer.
    os.environ["DYNAMODB_TABLE"] = args.target_table
    from bdo_common import dynamo
    from bdo_common.arsha_client import ArshaClient

    if args.export:
        _export(dynamo, args.items_file)
        return

    entries: list[dict[str, Any]] = _load_json(args.items_file)
    categories: dict[str, Any] = _load_json(args.categories_file)
    print(f"Loaded {len(entries)} tracked items; deriving categories from arsha.io...")
    index = build_category_index(ArshaClient(), categories)

    unclassified: list[int] = []
    for entry in entries:
        item_id = int(entry["id"])
        updates, classified = build_item_updates(entry, index)
        if not classified:
            unclassified.append(item_id)
        label = updates.get("category", "?")
        if args.dry_run:
            print(f"[DRY RUN] {item_id} ({entry.get('name', '')}) [{label}] <- {updates}")
        else:
            dynamo.update_item(item_id, updates)
            print(f"Seeded {item_id} ({entry.get('name', '')}) [{label}]")

    action = "previewed" if args.dry_run else "seeded"
    print(f"Done. {len(entries)} items {action}.")
    if unclassified:
        print(
            f"WARNING: no category derived for {len(unclassified)} item(s): {unclassified}. "
            "They are tracked but ungrouped -- add an explicit 'category' to the entry "
            "or extend categories.json."
        )


if __name__ == "__main__":
    main()
