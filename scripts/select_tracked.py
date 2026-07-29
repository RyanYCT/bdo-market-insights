"""Toggle which items are tracked -- resolve a preset/selection into the seed list.

Fully offline. Reads the committed market snapshot (``full_items.json``),
preset definitions (``presets.json``) and named sets (``track_sets.json``),
resolves the chosen selection to a list of item ids, and writes the tracked-item
list (``tracked_items.json``) that ``seed_items.py`` consumes. No arsha calls.

Selection is a named ``--preset`` (one or more, comma-separated), an ad-hoc
``--main``/``--sub`` market category, or a named ``--set``. With no selection
flag it shows an interactive menu that also accepts several comma-separated
numbers (e.g. ``9,10``); multiple presets are unioned. The selection is
**added** to the current tracked list by default (so picking a preset never
silently drops what you already track); pass ``--replace`` to overwrite the list
instead. Broad *resulting* sets (the ``all`` preset, or more than
``MAX_UNGUARDED_SELECTION`` tracked items) require confirmation (interactive) or
``--force`` (non-interactive).

``--min-grade``/``--max-grade`` further narrow any selection to a grade band
(grade codes: 0 White, 1 Green, 2 Blue, 3 Gold, 4 Orange, 5 Violet), reading the
grade baked into the snapshot. A preset may also declare its own ``min_grade``
default (e.g. ``accessories`` = Gold+); the CLI flags override it (pass
``--min-grade 0`` to re-include every grade). Items whose grade is unknown in the
snapshot are dropped whenever a grade bound applies.

    uv run python scripts/select_tracked.py                          # interactive menu
    uv run python scripts/select_tracked.py --preset deboreka,buffs  # union, adds to current
    uv run python scripts/select_tracked.py --preset ring --out scripts/data/tracked_items.json
    uv run python scripts/select_tracked.py --preset all --replace --force --out <path>
    uv run python scripts/select_tracked.py --main 20 --min-grade 3   # high-value accessories
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


def _preset_kwargs(
    name: str, presets: dict[str, Any], sets: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """select_ids kwargs for one preset name; second element is True for 'all'.

    A preset may carry its own ``min_grade``/``max_grade`` default (e.g.
    ``accessories`` limited to high-value items); those are folded into the
    kwargs here and can be overridden per-run by the ``--min-grade`` /
    ``--max-grade`` CLI flags.
    """
    if name not in presets or name.startswith("_"):
        _fail(f"unknown preset {name!r}; choose from: {_preset_names(presets)}")
    spec = presets[name]
    grade: dict[str, Any] = {}
    if spec.get("min_grade") is not None:
        grade["min_grade"] = int(spec["min_grade"])
    if spec.get("max_grade") is not None:
        grade["max_grade"] = int(spec["max_grade"])
    if spec.get("all"):
        return {"select_all": True, **grade}, True
    if "set" in spec:
        return {"ids": _set_ids(sets, spec["set"]), **grade}, False
    return {"main": spec.get("main"), "sub": spec.get("sub"), **grade}, False


def _apply_grade_override(kwargs: dict[str, Any], args: argparse.Namespace) -> None:
    """Override any preset grade default with the CLI flags, in place.

    ``--min-grade``/``--max-grade`` always win when given (e.g. ``--min-grade 0``
    re-includes everything a preset would otherwise have limited to high grades).
    """
    if args.min_grade is not None:
        kwargs["min_grade"] = args.min_grade
    if args.max_grade is not None:
        kwargs["max_grade"] = args.max_grade


def _grade_suffix(kwargs: dict[str, Any]) -> str:
    """Human-readable ' (grade ...)' suffix describing the applied grade band."""
    lo, hi = kwargs.get("min_grade"), kwargs.get("max_grade")
    if lo is None and hi is None:
        return ""
    if lo is not None and hi is not None:
        return f" (grade {lo}-{hi})"
    return f" (grade >= {lo})" if lo is not None else f" (grade <= {hi})"


def _resolve_selection(
    args: argparse.Namespace, presets: dict[str, Any], sets: dict[str, Any], catalog: list[Any]
) -> tuple[list[int], str, bool]:
    """Resolve the CLI selection into (sorted ids, label, select_all).

    ``--preset`` accepts one or more comma-separated names whose selections are
    unioned; ``--set`` and ``--main``/``--sub`` are single ad-hoc selectors.
    """
    from bdo_common.tracking import select_ids

    if args.preset:
        names = [n.strip() for n in args.preset.split(",") if n.strip()]
        if not names:
            _fail("no preset given")
        ids: set[int] = set()
        select_all = False
        suffixes: set[str] = set()
        for name in names:
            kwargs, is_all = _preset_kwargs(name, presets, sets)
            _apply_grade_override(kwargs, args)
            select_all = select_all or is_all
            ids.update(select_ids(catalog, **kwargs))
            suffixes.add(_grade_suffix(kwargs))
        base = f"preset {names[0]}" if len(names) == 1 else f"presets {', '.join(names)}"
        # One shared suffix when every preset resolved to the same grade band.
        label = base + (suffixes.pop() if len(suffixes) == 1 else "")
        return sorted(ids), label, select_all
    if args.set:
        kwargs = {"ids": _set_ids(sets, args.set)}
        _apply_grade_override(kwargs, args)
        label = f"set '{args.set}'{_grade_suffix(kwargs)}"
        return sorted(select_ids(catalog, **kwargs)), label, False
    if args.main is not None:
        kwargs = {"main": args.main, "sub": args.sub}
        _apply_grade_override(kwargs, args)
        base = f"main {args.main}" + (f" sub {args.sub}" if args.sub is not None else "")
        return sorted(select_ids(catalog, **kwargs)), base + _grade_suffix(kwargs), False
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


def _interactive_presets(presets: dict[str, Any]) -> str:
    """Show a numbered preset menu; return the chosen name(s), comma-joined.

    Accepts one or more numbers, comma-separated (e.g. ``9,10``); duplicates are
    collapsed, order preserved.
    """
    names = _preset_names(presets)
    print("Select track preset(s):")
    for i, name in enumerate(names, start=1):
        print(f"  {i}. {name}")
    raw = input("Enter number(s), comma-separated (e.g. 9,10): ").strip()
    chosen: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if not token.isdigit() or not (1 <= int(token) <= len(names)):
            _fail(f"invalid choice: {token!r}")
        name = names[int(token) - 1]
        if name not in chosen:
            chosen.append(name)
    if not chosen:
        _fail("no choice given")
    return ",".join(chosen)


def _sort_records(
    records: list[dict[str, Any]], catalog_by_id: dict[int, Any]
) -> list[dict[str, Any]]:
    """Sort records by (main, sub, id); ids absent from the snapshot sort last."""

    def key(rec: dict[str, Any]) -> tuple[int, int, int]:
        entry = catalog_by_id.get(int(rec["id"]))
        if entry is None:
            return (10**9, 10**9, int(rec["id"]))
        return (entry.main_category, entry.sub_category, int(rec["id"]))

    return sorted(records, key=key)


def _merge_records(
    existing: list[dict[str, Any]],
    selected: list[int],
    catalog_by_id: dict[int, Any],
    *,
    replace: bool,
) -> list[dict[str, Any]]:
    """Merge the selection into the current list (default), or replace it.

    Existing entries are preserved as-is (keeping any manual fields); only newly
    selected ids are appended. ``cron_profile`` is intentionally not written --
    it is derived at seed time from series membership (track_sets.json), so
    tracked_items.json stays a pure id/name list.
    """
    base = [] if replace else list(existing)
    have = {int(record["id"]) for record in base}
    for item_id in selected:
        if item_id not in have:
            base.append({"id": item_id, "name": catalog_by_id[item_id].name})
            have.add(item_id)
    return _sort_records(base, catalog_by_id)


def _print_summary(existing_ids: set[int], final_ids: set[int], *, replace: bool) -> None:
    """Print the add/replace outcome vs the current tracked list."""
    added = final_ids - existing_ids
    removed = existing_ids - final_ids
    unchanged = existing_ids & final_ids
    mode = "replace" if replace else "add"
    print(f"  mode: {mode}  |  current {len(existing_ids)} tracked -> new {len(final_ids)}")
    print(f"  + {len(added)} added, {len(unchanged)} unchanged, - {len(removed)} removed")
    if removed:
        rem = sorted(removed)
        print(f"  removed ids: {rem[:20]}{' ...' if len(rem) > 20 else ''}")
        print("  (these stop being tracked; run seed_items.py --reconcile to untrack in DynamoDB)")


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
        "--min-grade",
        type=int,
        dest="min_grade",
        help="keep only items with grade >= N (e.g. 3 = high-value Gold+); "
        "overrides any per-preset default. Pass 0 to include all grades.",
    )
    parser.add_argument(
        "--max-grade",
        type=int,
        dest="max_grade",
        help="keep only items with grade <= N; overrides any per-preset default",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="overwrite the tracked list with the selection (default: add to it)",
    )
    parser.add_argument(
        "--force", action="store_true", help="bypass the broad-selection guard (non-interactive)"
    )
    args = parser.parse_args()

    from bdo_common.tracking import catalog_index, needs_confirmation

    catalog = _load_catalog(args.catalog)
    presets = _load_json(args.presets)
    sets = _load_json(args.sets)

    # No selection flag -> interactive preset menu, defaulting the output to the
    # curated tracked list (still requires a final y/N to write).
    interactive = not (args.preset or args.set or args.main is not None)
    if interactive:
        args.preset = _interactive_presets(presets)
        if args.out is None:
            args.out = _TRACKED_ITEMS_FILE

    selected, label, select_all = _resolve_selection(args, presets, sets, catalog)
    if not selected:
        _fail(f"selection ({label}) matched no items in {args.catalog.name}")

    out_path = args.out or _TRACKED_ITEMS_FILE
    existing_records: list[dict[str, Any]] = _load_json(out_path) if out_path.exists() else []
    existing_ids = {int(record["id"]) for record in existing_records}

    final_records = _merge_records(
        existing_records, selected, catalog_index(catalog), replace=args.replace
    )
    final_ids = {int(record["id"]) for record in final_records}

    print(f"Selection: {label}")
    _print_summary(existing_ids, final_ids, replace=args.replace)

    # Guard on the resulting tracked-set size (that is what the ETL polls).
    guarded = needs_confirmation(len(final_ids), select_all=select_all)
    if guarded:
        warning = (
            f"This would track {len(final_ids)} items -- broad; it will enlarge the hourly ETL."
        )
        if interactive:
            if input(f"{warning}\nProceed? [y/N]: ").strip().lower() != "y":
                print("Aborted.")
                return
        elif not args.force:
            _fail(f"{warning} Re-run with --force to proceed.")

    if args.out is None:
        print(f"Preview only ({len(final_records)} items). Re-run with --out to write the list.")
        return

    if (
        interactive
        and input(f"Write {len(final_records)} items to {args.out}? [y/N]: ").strip().lower()
        != "y"
    ):
        print("Aborted.")
        return

    args.out.write_text(
        json.dumps(final_records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    verb = "replaced with" if args.replace else "now"
    print(f"{out_path.name} {verb} {len(final_records)} items. Seed: make seed-data STAGE=<stage>")


if __name__ == "__main__":
    main()
