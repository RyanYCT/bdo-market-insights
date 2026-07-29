"""Offline track-selection logic behind the seeding pipeline.

Pure functions shared by the seed/toggle scripts (``build_market_catalog``,
``select_tracked``, ``seed_items``): enumerate the market taxonomy, select item
ids by category or explicit set, and derive per-item tracking fields from the
committed snapshot -- so seeding never depends on arsha at run time. Network is
injected (a ``fetch`` callable), keeping everything here deterministic and
testable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from bdo_common.models import MarketListItem

#: Selections at or above this size (or ``all``) require explicit confirmation
#: in the toggle -- tracking a whole main category can add hundreds of items to
#: the hourly ETL, so broad selects are guarded (never silent).
MAX_UNGUARDED_SELECTION = 100

#: Coarse categories whose items are enhanceable (consume cron stones), so they
#: default to the ``standard`` cron profile. Everything else defaults to
#: ``none`` (not enhanceable, e.g. pearl/functional consumables). Named series
#: (track_sets.json) still override this with their own profile.
ENHANCEABLE_CATEGORIES = frozenset({"accessory"})

#: A ``fetch(main, sub) -> items`` callable; an empty list marks a nonexistent
#: (or empty) combination -- the enumeration stop signal.
FetchMarketList = Callable[[int, int], list[MarketListItem]]


def main_category_codes(max_main: int = 85) -> list[int]:
    """Candidate main-category probe codes: 1, then 5, 10, ..., ``max_main``."""
    return [1, *range(5, max_main + 1, 5)]


def enumerate_taxonomy(
    fetch: FetchMarketList, *, max_main: int = 85, max_sub: int = 30
) -> list[MarketListItem]:
    """Enumerate the market taxonomy into a flat, de-duplicated, sorted list.

    For each main code, sub codes are probed ``1, 2, 3, ...`` until ``fetch``
    returns an empty list (the combination does not exist). Items are
    de-duplicated by id (last write wins) and returned sorted by id.
    """
    by_id: dict[int, MarketListItem] = {}
    for main in main_category_codes(max_main):
        for sub in range(1, max_sub + 1):
            items = fetch(main, sub)
            if not items:
                break
            for item in items:
                by_id[item.item_id] = item
    return [by_id[i] for i in sorted(by_id)]


def parse_catalog(rows: list[dict[str, Any]]) -> list[MarketListItem]:
    """Parse ``full_items.json`` rows into models.

    Rows are ``{id, name, main, sub}`` plus an optional ``grade`` (merged from
    ``util/db`` when the snapshot was built); a missing/null ``grade`` parses to
    ``None`` so older snapshots without the field still load.
    """
    return [
        MarketListItem(
            item_id=int(row["id"]),
            name=str(row["name"]),
            main_category=int(row["main"]),
            sub_category=int(row["sub"]),
            grade=(None if row.get("grade") is None else int(row["grade"])),
        )
        for row in rows
    ]


def catalog_index(catalog: list[MarketListItem]) -> dict[int, MarketListItem]:
    """Index a catalog list by item id for O(1) lookup."""
    return {entry.item_id: entry for entry in catalog}


def select_ids(
    catalog: list[MarketListItem],
    *,
    main: int | None = None,
    sub: int | None = None,
    ids: list[int] | None = None,
    select_all: bool = False,
    min_grade: int | None = None,
    max_grade: int | None = None,
) -> list[int]:
    """Resolve a selection against the catalog into a sorted id list.

    One mode is expected, in precedence order: ``select_all`` (everything),
    explicit ``ids`` (intersected with the catalog so a stale id is dropped),
    or a category filter by ``main`` (whole main category) and optionally
    ``sub`` (one category). Passing none returns an empty list.

    When ``min_grade`` and/or ``max_grade`` are given, the resolved selection is
    additionally filtered to items whose snapshot ``grade`` falls in that
    (inclusive) band -- e.g. ``min_grade=3`` keeps only high-value items
    (Gold/Orange/Violet). Items with an unknown grade (``None``) are dropped
    whenever any grade bound is set.
    """
    index = catalog_index(catalog)
    if select_all:
        candidate = list(index)
    elif ids is not None:
        candidate = [i for i in ids if i in index]
    elif main is not None:
        candidate = [
            entry.item_id
            for entry in catalog
            if entry.main_category == main and (sub is None or entry.sub_category == sub)
        ]
    else:
        return []
    return sorted(_filter_by_grade(candidate, index, min_grade=min_grade, max_grade=max_grade))


def _filter_by_grade(
    ids: list[int],
    index: dict[int, MarketListItem],
    *,
    min_grade: int | None,
    max_grade: int | None,
) -> list[int]:
    """Keep ids whose snapshot grade is within [min_grade, max_grade] (inclusive).

    A no-op when both bounds are ``None``. When any bound is set, items with an
    unknown grade (``None``) are excluded (they cannot be proven high-value).
    """
    if min_grade is None and max_grade is None:
        return ids
    kept: list[int] = []
    for item_id in ids:
        grade = index[item_id].grade
        if grade is None:
            continue
        if min_grade is not None and grade < min_grade:
            continue
        if max_grade is not None and grade > max_grade:
            continue
        kept.append(item_id)
    return kept


def category_label(main: int, sub: int, category_map: dict[str, str]) -> str | None:
    """Coarse category for a ``(main, sub)`` via a ``"main:sub" -> label`` map."""
    return category_map.get(f"{main}:{sub}")


def default_cron_profile(category: str | None) -> str:
    """Cron profile for an item with no explicit series override.

    Enhanceable items (see ``ENHANCEABLE_CATEGORIES``) use the ``standard``
    accessory profile; everything else -- including unclassified items and
    non-enhanceable consumables (pearl/functional) -- is ``none``.
    """
    return "standard" if category in ENHANCEABLE_CATEGORIES else "none"


def build_tracked_updates(
    item_id: int,
    *,
    series_profile: str | None = None,
    index: dict[int, MarketListItem],
    category_map: dict[str, str],
    model_id: str | None = None,
) -> tuple[dict[str, str], bool]:
    """Build the DynamoDB partial-update for one tracked id, fully offline.

    ``main``/``sub`` come from the committed catalog snapshot and the coarse
    ``category`` from ``category_map`` -- no arsha. ``cron_profile`` is the
    item's series override (``series_profile``) if it belongs to a series that
    declares one, else the category default (accessory -> ``standard``, else
    ``none``). Returns ``(updates, classified)``; ``classified`` is False when
    the id is absent from the snapshot or its ``(main, sub)`` has no coarse
    label (tracked but ungrouped).
    """
    updates: dict[str, str] = {"tracked": "true"}
    if model_id is not None:
        updates["model_id"] = model_id

    entry = index.get(item_id)
    label: str | None = None
    if entry is not None:
        updates["main_category"] = str(entry.main_category)
        updates["sub_category"] = str(entry.sub_category)
        label = category_label(entry.main_category, entry.sub_category, category_map)
        if label is not None:
            updates["category"] = label

    updates["cron_profile"] = series_profile or default_cron_profile(label)
    return updates, entry is not None and label is not None


def cron_overrides(sets: Mapping[str, Any]) -> dict[int, str]:
    """Map ``id -> cron_profile`` from any named set that declares a ``cron_profile``.

    Applied as a cross-cutting layer so an item keeps its series cron-stone
    profile (e.g. the Deboreka series -> ``"deboreka"``) regardless of which
    preset selected it. Sets without a ``cron_profile`` contribute nothing (the
    items fall back to the default ``"standard"``).
    """
    out: dict[int, str] = {}
    for spec in sets.values():
        if not isinstance(spec, dict):
            continue
        profile = spec.get("cron_profile")
        if not profile:
            continue
        for item_id in spec.get("ids", []):
            out[int(item_id)] = str(profile)
    return out


def ids_to_untrack(current_tracked: set[int], selected: set[int]) -> list[int]:
    """Ids currently tracked but absent from the new selection (reconcile mode)."""
    return sorted(current_tracked - selected)


def needs_confirmation(count: int, *, select_all: bool = False) -> bool:
    """True when a selection is broad enough to require explicit confirmation."""
    return select_all or count > MAX_UNGUARDED_SELECTION
