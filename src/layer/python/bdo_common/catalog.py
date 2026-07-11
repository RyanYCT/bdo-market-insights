"""Full-catalog sync: fetch arsha ``util/db`` per language, merge, and upsert.

Shared by the ``catalogSync`` Lambda and the ``seed_catalog`` script. English
is the canonical name (stored on ``name``); other languages go in the ``names``
map. ``grade`` is language-independent. Writes are partial upserts, so the
ETL-owned attributes on tracked items are preserved (ADR-0018).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from bdo_common import dynamo
from bdo_common.arsha_client import DEFAULT_LANG, ArshaClient
from bdo_common.models import CatalogEntry, MergedCatalogItem

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CatalogSyncStats:
    """Outcome of a catalog sync run."""

    total: int
    new: int
    langs: list[str] = field(default_factory=list)
    fetched: dict[str, int] = field(default_factory=dict)  # per-language item counts
    skipped: bool = False  # True when the run was skipped (default language failed)


def merge_catalog(
    by_lang: dict[str, list[CatalogEntry]], *, default_lang: str = DEFAULT_LANG
) -> list[MergedCatalogItem]:
    """Merge per-language ``util/db`` rows into one item per id.

    ``name`` is taken from ``default_lang`` (English); if an id is missing there
    it falls back to the first language that has it (so an item never loses its
    name). ``names`` holds the non-default localizations. ``grade`` is taken
    from ``default_lang`` and falls back to any language that carries it.
    """
    indexed = {lang: {e.item_id: e for e in entries} for lang, entries in by_lang.items()}
    langs = list(by_lang.keys())
    all_ids = sorted({item_id for index in indexed.values() for item_id in index})

    merged: list[MergedCatalogItem] = []
    for item_id in all_ids:
        name: str | None = None
        grade: int | None = None
        default_entry = indexed.get(default_lang, {}).get(item_id)
        if default_entry is not None:
            name, grade = default_entry.name, default_entry.grade
        # Fall back to any language that has the id / a grade.
        for lang in langs:
            entry = indexed[lang].get(item_id)
            if entry is None:
                continue
            if name is None:
                name = entry.name
            if grade is None:
                grade = entry.grade
        names = {
            lang: entry.name
            for lang in langs
            if lang != default_lang and (entry := indexed[lang].get(item_id)) is not None
        }
        merged.append(
            MergedCatalogItem(item_id=item_id, name=name or "", names=names, grade=grade)
        )
    return merged


def sync_catalog(
    client: ArshaClient,
    langs: list[str],
    *,
    default_lang: str = DEFAULT_LANG,
    max_workers: int = 16,
) -> CatalogSyncStats:
    """Fetch ``util/db`` for each language, merge by id, and upsert the catalog.

    A failed/empty fetch yields no items for that language (arsha client logs
    and returns empty), so a bad run upserts whatever was retrieved rather than
    deleting anything -- the sync is strictly additive.
    """
    by_lang = {lang: client.fetch_item_db(lang) for lang in langs}
    fetched = {lang: len(entries) for lang, entries in by_lang.items()}

    # The default language supplies the canonical (English) name. If it failed
    # to fetch (empty), skip the run rather than overwriting every item's name
    # with a fallback from another language.
    if not by_lang.get(default_lang):
        logger.error(
            "catalog sync skipped: default language %r returned no items (fetch failed)",
            default_lang,
        )
        return CatalogSyncStats(total=0, new=0, langs=langs, fetched=fetched, skipped=True)

    failed = [lang for lang, count in fetched.items() if count == 0]
    if failed:
        # Non-default languages that failed are simply not updated this run;
        # existing localized names are preserved (empty names are not written).
        logger.warning(
            "catalog sync: languages returned no data, kept existing values: %s", failed
        )

    merged = merge_catalog(by_lang, default_lang=default_lang)
    total, new = dynamo.bulk_upsert_catalog_items(merged, max_workers=max_workers)
    logger.info(
        "catalog sync complete",
        extra={"total": total, "new": new, "langs": langs, "fetched": fetched},
    )
    return CatalogSyncStats(total=total, new=new, langs=langs, fetched=fetched, skipped=False)
