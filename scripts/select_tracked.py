"""Toggle which items are tracked -- resolve a preset/selection into the seed list.

Fully offline. Reads the committed market snapshot (``full_items.json``),
preset definitions (``presets.json``) and named sets (``track_sets.json``),
resolves the chosen selection to a list of item ids, and writes the tracked-item
list (``tracked_items.json``) that ``seed_items.py`` consumes. No arsha calls.

Selection is one of: a named ``--preset``, an ad-hoc ``--main``/``--sub`` market
category, or a named ``--set``. With no selection flag it shows an interactive
preset menu. Broad selections (the ``all`` preset, or more than
``MAX_UNGUARDED_SELECTION`` items) require confirmation (interactive) or
``--force`` (non-interactive), because tracking a whole category can add
hundreds of items to the hourly ETL.

    uv run python scripts/select_tracked.py                       # interactive menu
    uv run python scripts/select_tracked.py --preset accessories  # preview (add --out to write)
    uv run python scripts/select_tracked.py --main 20 --sub 1
    uv run python scripts/select_tracked.py --preset all --force --out <path>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).parent / "data"
_CATALOG_FILE = _DATA_DIR / "full_items.json"
_PRESETS_FILE = _DATA_DIR / "presets.json"
_SETS_FILE = _DATA_DIR / "track_sets.json"
_TRACKED_ITEMS_FILE = _DATA_DIR / "tracked_items.json"


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _load_catalog(path: Path) -> list[Any]:
    """Load full_items.json into MarketListItem objects."""
    from bdo_common.tracking import parse_catalog

    return parse_catalog(_load_json(path))


def _selection_kwargs(
    args: argparse.Namespace, presets: dict[str, Any], sets: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """Translate CLI args into select_ids kwargs plus a human-readable label."""
    if args.preset:
        if args.preset not in presets or args.preset.startswith("_"):
            _fail(f"unknown preset {args.preset!r}; choose from: {_preset_names(presets)}")
        spec = presets[args.preset]
        if spec.get("all"):
            return {"select_all": True}, f"preset '{args.preset}' (ALL items)"
        if "set" in spec:
            return {
                "ids": _set_ids(sets, spec["set"])
            }, f"preset '{args.preset}' (set {spec['set']})"
        return {"main": spec.get("main"), "sub": spec.get("sub")}, f"preset '{args.preset}'"
    if args.set:
        return {"ids": _set_ids(sets, args.set)}, f"set '{args.set}'"
    if args.main is not None:
        label = f"main {args.main}" + (f" sub {args.sub}" if args.sub is not None else "")
        return {"main": args.main, "sub": args.sub}, label
    _fail("no selection given; use --preset / --main[/--sub] / --set, or run with no flags")


def _set_ids(sets: dict[str, Any], name: str) -> list[int]:
    if name not in sets or name.startswith("_"):
        _fail(f"unknown set {name!r}; choose from: {_set_names(sets)}")
    return [int(i) for i in sets[name]["ids"]]


def _preset_names(presets: dict[str, Any]) -> list[str]:
    return [k for k in presets if not k.startswith("_")]


def _set_names(sets: dict[str, Any]) -> list[str]:
    return [k for k in sets if not k.startswith("_")]


def _fail(message: str) -> Any:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def _interactive_preset(presets: dict[str, Any]) -> str:
    """Show a numbered preset menu and return the chosen preset name."""
    names = _preset_names(presets)
    print("Select a track preset:")
    for i, name in enumerate(names, start=1):
        print(f"  {i}. {name}")
    choice = input("Enter number: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(names)):
        _fail("invalid choice")
    return names[int(choice) - 1]


def _build_records(selected: list[int], catalog_by_id: dict[int, Any]) -> list[dict[str, Any]]:
    """Build the tracked_items.json records ({id, name}), sorted by (main, sub, id).

    ``cron_profile`` is intentionally NOT written here: it is derived at seed
    time from series membership (track_sets.json), so tracked_items.json stays a
    pure id/name list.
    """
    triples = []
    for item_id in selected:
        entry = catalog_by_id[item_id]
        record: dict[str, Any] = {"id": item_id, "name": entry.name}
        triples.append((entry.main_category, entry.sub_category, record))
    triples.sort(key=lambda t: (t[0], t[1], t[2]["id"]))
    return [record for _, _, record in triples]


def _print_diff(selected: set[int], out_path: Path) -> None:
    """Print the added/removed diff of this selection vs the existing out file."""
    existing: set[int] = set()
    if out_path.exists():
        existing = {int(e["id"]) for e in _load_json(out_path)}
    added = sorted(selected - existing)
    removed = sorted(existing - selected)
    print(f"Selected {len(selected)} items (was {len(existing)} in {out_path.name}).")
    print(f"  + {len(added)} added, - {len(removed)} removed (vs current file)")
    if removed:
        print(f"  removed ids: {removed[:20]}{' ...' if len(removed) > 20 else ''}")
        print("  NOTE: seeding is additive; use seed_items.py --reconcile to untrack these.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resolve a track preset/selection into a seed list"
    )
    parser.add_argument("--preset", help=f"named preset (see {_PRESETS_FILE.name})")
    parser.add_argument("--main", type=int, help="market main-category code (ad-hoc selection)")
    parser.add_argument("--sub", type=int, help="market sub-category code (requires --main)")
    parser.add_argument("--set", dest="set", help=f"named set (see {_SETS_FILE.name})")
    parser.add_argument("--out", type=Path, help="write the list here (omit to preview only)")
    parser.add_argument("--catalog", type=Path, default=_CATALOG_FILE, help="market snapshot file")
    parser.add_argument("--presets", type=Path, default=_PRESETS_FILE, help="presets file")
    parser.add_argument("--sets", type=Path, default=_SETS_FILE, help="named-sets file")
    parser.add_argument(
        "--force", action="store_true", help="bypass the broad-selection guard (non-interactive)"
    )
    args = parser.parse_args()

    from bdo_common.tracking import catalog_index, needs_confirmation, select_ids

    catalog = _load_catalog(args.catalog)
    presets = _load_json(args.presets)
    sets = _load_json(args.sets)

    # No selection flag -> interactive preset menu, defaulting the output to the
    # curated tracked list (still requires a final y/N to write).
    interactive = not (args.preset or args.set or args.main is not None)
    if interactive:
        args.preset = _interactive_preset(presets)
        if args.out is None:
            args.out = _TRACKED_ITEMS_FILE

    kwargs, label = _selection_kwargs(args, presets, sets)
    selected = select_ids(catalog, **kwargs)
    if not selected:
        _fail(f"selection ({label}) matched no items in {args.catalog.name}")

    print(f"Selection: {label}")
    out_path = args.out or _TRACKED_ITEMS_FILE
    _print_diff(set(selected), out_path)

    # Guard broad selections.
    guarded = needs_confirmation(len(selected), select_all=bool(kwargs.get("select_all")))
    if guarded:
        warning = f"This selects {len(selected)} items -- broad; it will enlarge the hourly ETL."
        if interactive:
            if input(f"{warning}\nProceed? [y/N]: ").strip().lower() != "y":
                print("Aborted.")
                return
        elif not args.force:
            _fail(f"{warning} Re-run with --force to proceed.")

    records = _build_records(selected, catalog_index(catalog))

    if args.out is None:
        print(f"Preview only ({len(records)} items). Re-run with --out to write the list.")
        return

    if (
        interactive
        and input(f"Write {len(records)} items to {args.out}? [y/N]: ").strip().lower() != "y"
    ):
        print("Aborted.")
        return

    args.out.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(records)} items to {args.out}. Seed with: make seed-data STAGE=<stage>")


if __name__ == "__main__":
    main()
