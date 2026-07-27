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
    """Parse ``full_item_list.json`` rows (``{id, name, main, sub}``) into models."""
    return [
        MarketListItem(
            item_id=int(row["id"]),
            name=str(row["name"]),
            main_category=int(row["main"]),
            sub_category=int(row["sub"]),
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
) -> list[int]:
    """Resolve a selection against the catalog into a sorted id list.

    One mode is expected, in precedence order: ``select_all`` (everything),
    explicit ``ids`` (intersected with the catalog so a stale id is dropped),
    or a category filter by ``main`` (whole main category) and optionally
    ``sub`` (one category). Passing none returns an empty list.
    """
    present = {entry.item_id for entry in catalog}
    if select_all:
        return sorted(present)
    if ids is not None:
        return sorted(i for i in ids if i in present)
    if main is None:
        return []
    result = [
        entry.item_id
        for entry in catalog
        if entry.main_category == main and (sub is None or entry.sub_category == sub)
    ]
    return sorted(result)


def category_label(main: int, sub: int, category_map: dict[str, str]) -> str | None:
    """Coarse category for a ``(main, sub)`` via a ``"main:sub" -> label`` map."""
    return category_map.get(f"{main}:{sub}")


def build_tracked_updates(
    item_id: int,
    *,
    cron_profile: str,
    index: dict[int, MarketListItem],
    category_map: dict[str, str],
    model_id: str | None = None,
) -> tuple[dict[str, str], bool]:
    """Build the DynamoDB partial-update for one tracked id, fully offline.

    ``main``/``sub`` come from the committed catalog snapshot and the coarse
    ``category`` from ``category_map`` -- no arsha. Returns ``(updates,
    classified)``; ``classified`` is False when the id is absent from the
    snapshot or its ``(main, sub)`` has no coarse label (tracked but ungrouped).
    """
    updates: dict[str, str] = {"tracked": "true", "cron_profile": cron_profile}
    if model_id is not None:
        updates["model_id"] = model_id

    entry = index.get(item_id)
    if entry is None:
        return updates, False

    updates["main_category"] = str(entry.main_category)
    updates["sub_category"] = str(entry.sub_category)
    label = category_label(entry.main_category, entry.sub_category, category_map)
    if label is None:
        return updates, False
    updates["category"] = label
    return updates, True


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
