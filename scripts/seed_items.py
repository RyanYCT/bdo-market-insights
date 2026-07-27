"""Seed the tracked-item set into bdo-<stage>-items from the curated list.

Fully offline. Reads ``scripts/data/tracked_items.json`` (the items to track),
``scripts/data/full_items.json`` (the committed market snapshot),
``scripts/data/categories.json`` (``main:sub`` -> coarse category) and
``scripts/data/track_sets.json`` (named series), and writes ``tracked=true`` +
the sparse tracked-index marker + ``cron_profile`` +
``main_category``/``sub_category``/``category`` as a **partial upsert** -- so the
catalog-owned fields (``name``/``grade``/``names``) that ``seed_catalog`` /
``catalogSync`` populate are preserved (ADR-0018). No arsha calls; regenerate
the snapshot occasionally with ``build_market_catalog.py``.

``cron_profile`` is derived **programmatically** from series membership: an item
that belongs to a named set declaring a ``cron_profile`` (e.g. the ``deboreka``
series) gets that profile; everything else defaults to ``"standard"``. It is not
hand-entered per item.

Build or change ``tracked_items.json`` with ``select_tracked.py`` (the
preset-driven toggle). By default seeding is **additive** (it only marks the
listed items tracked); ``--reconcile`` also untracks anything currently tracked
but no longer in the list.

    uv run python scripts/seed_items.py --target-table bdo-dev-items
    uv run python scripts/seed_items.py --target-table bdo-dev-items --dry-run
    uv run python scripts/seed_items.py --target-table bdo-prod-items --reconcile
    # Regenerate the list from a running environment's current tracked set:
    uv run python scripts/seed_items.py --target-table bdo-prod-items --export
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).parent / "data"
_TRACKED_ITEMS_FILE = _DATA_DIR / "tracked_items.json"
_CATALOG_FILE = _DATA_DIR / "full_items.json"
_CATEGORIES_FILE = _DATA_DIR / "categories.json"
_SETS_FILE = _DATA_DIR / "track_sets.json"


def _load_json(path: Path) -> Any:
    """Load and parse a JSON file."""
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _category_map(categories: dict[str, Any]) -> dict[str, str]:
    """Reduce categories.json (``main:sub`` -> ``{category, type}``) to labels.

    Comment keys (starting with ``_``) are skipped.
    """
    return {
        key: str(spec["category"]) for key, spec in categories.items() if not key.startswith("_")
    }


def _export(dynamo: Any, path: Path) -> None:
    """Write the current tracked set from the table to the items file."""
    items = dynamo.list_tracked_items()
    records: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda i: i.id):
        # cron_profile is derived from series membership (track_sets.json), not
        # stored per entry, so the exported list stays a pure id/name list.
        rec: dict[str, Any] = {"id": item.id, "name": item.name}
        if item.model_id != "accessory_v1":
            rec["model_id"] = item.model_id
        records.append(rec)
    path.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Exported {len(records)} tracked items to {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the tracked-item set into the per-stage items table (offline)"
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
        "--catalog-file",
        type=Path,
        default=_CATALOG_FILE,
        help="Committed market snapshot (default: scripts/data/full_items.json)",
    )
    parser.add_argument(
        "--categories-file",
        type=Path,
        default=_CATEGORIES_FILE,
        help="Taxonomy map for category derivation (default: scripts/data/categories.json)",
    )
    parser.add_argument(
        "--sets-file",
        type=Path,
        default=_SETS_FILE,
        help="Named series for cron_profile derivation (default: scripts/data/track_sets.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the derived updates without writing to the table",
    )
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="Also untrack items currently tracked but absent from the list (destructive)",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Write the current tracked set from --target-table to --items-file, then exit",
    )
    args = parser.parse_args()

    # dynamo reads DYNAMODB_TABLE at import, so set it before importing the layer.
    os.environ["DYNAMODB_TABLE"] = args.target_table
    from bdo_common import dynamo, tracking

    if args.export:
        _export(dynamo, args.items_file)
        return

    entries: list[dict[str, Any]] = _load_json(args.items_file)
    catalog = tracking.parse_catalog(_load_json(args.catalog_file))
    index = tracking.catalog_index(catalog)
    category_map = _category_map(_load_json(args.categories_file))
    # id -> cron_profile from any series (track_sets.json) that declares one.
    cron_by_id = tracking.cron_overrides(_load_json(args.sets_file))
    print(
        f"Loaded {len(entries)} tracked items; deriving categories offline "
        f"from {args.catalog_file.name} ({len(catalog)} items)..."
    )

    unclassified: list[int] = []
    selected: set[int] = set()
    for entry in entries:
        item_id = int(entry["id"])
        selected.add(item_id)
        updates, classified = tracking.build_tracked_updates(
            item_id,
            cron_profile=cron_by_id.get(item_id, "standard"),
            index=index,
            category_map=category_map,
            model_id=entry.get("model_id"),
        )
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

    if args.reconcile:
        current = {item.id for item in dynamo.list_tracked_items()}
        stale = tracking.ids_to_untrack(current, selected)
        for item_id in stale:
            if args.dry_run:
                print(f"[DRY RUN] untrack {item_id} (tracked but not in list)")
            else:
                dynamo.update_item(item_id, {"tracked": "false"})
                print(f"Untracked {item_id} (no longer in list)")
        verb = "would untrack" if args.dry_run else "untracked"
        print(f"Reconcile: {verb} {len(stale)} item(s) no longer in the list.")

    if unclassified:
        print(
            f"WARNING: no category derived for {len(unclassified)} item(s): {unclassified}. "
            "They are tracked but ungrouped -- add their (main:sub) to categories.json, "
            "or rebuild the snapshot with build_market_catalog.py if they are missing from it."
        )


if __name__ == "__main__":
    main()
