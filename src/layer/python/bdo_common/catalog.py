"""Full-catalog sync: fetch arsha ``util/db`` per language, merge, and upsert.

Shared by the ``catalogSync`` Lambda and the ``seed_catalog`` script. English
is the canonical name (stored on ``name``); other languages go in the ``names``
map. ``grade`` is language-independent. Writes are partial upserts, so the
ETL-owned attributes on tracked items are preserved (ADR-0018).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

import boto3
from botocore.exceptions import ClientError

from bdo_common import dynamo
from bdo_common.arsha_client import DEFAULT_LANG, ArshaClient
from bdo_common.models import CatalogEntry, MergedCatalogItem

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CatalogSyncStats:
    """Outcome of a catalog sync run."""

    total: int  # merged items considered this run
    new: int  # newly created items among those written
    langs: list[str] = field(default_factory=list)
    fetched: dict[str, int] = field(default_factory=dict)  # per-language item counts
    skipped: bool = False  # True when the run was skipped (default language failed)
    written: int = 0  # items actually upserted (the changed subset)
    unchanged: bool = False  # True when the checksum matched and writes were skipped


def catalog_checksum(items: list[MergedCatalogItem]) -> str:
    """Stable SHA-256 digest over the stored fields (id, name, grade, names).

    Order-independent (items are sorted by id). A rename, re-grade,
    localized-name change, or any add/remove changes the digest, so it is a safe
    "did anything change" signal -- unlike an item count, which is unchanged by
    a rename, re-grade, or an offsetting add+remove.
    """
    digest = hashlib.sha256()
    for item in sorted(items, key=lambda m: m.item_id):
        localized = ";".join(f"{lang}={item.names[lang]}" for lang in sorted(item.names))
        digest.update(f"{item.item_id}\x1f{item.name}\x1f{item.grade}\x1f{localized}\x1e".encode())
    return digest.hexdigest()


def diff_catalog(
    merged: list[MergedCatalogItem],
    current: dict[int, tuple[str, int | None, dict[str, str]]],
) -> list[MergedCatalogItem]:
    """Return only the items that need writing versus the current stored state.

    An item is written when it is new, or its ``name``/``grade`` changed, or it
    carries localized ``names`` that differ. An empty ``names`` map (a language
    that failed to fetch this run) is not treated as a change -- mirroring the
    upsert, which never writes an empty names map and so preserves existing
    localized names.
    """
    changed: list[MergedCatalogItem] = []
    for item in merged:
        cur = current.get(item.item_id)
        if cur is None:
            changed.append(item)
            continue
        cur_name, cur_grade, cur_names = cur
        if (
            item.name != cur_name
            or item.grade != cur_grade
            or item.names
            and item.names != cur_names
        ):
            changed.append(item)
    return changed


def _read_checksum(param: str, ssm_client: Any) -> str | None:
    """Read the stored catalog checksum from SSM, or None if it doesn't exist."""
    try:
        value: str = ssm_client.get_parameter(Name=param)["Parameter"]["Value"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ParameterNotFound":
            return None
        raise
    return value


def _write_checksum(param: str, value: str, ssm_client: Any) -> None:
    """Persist the catalog checksum to SSM (String parameter, overwrite)."""
    ssm_client.put_parameter(Name=param, Value=value, Type="String", Overwrite=True)


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
    checksum_param: str | None = None,
    ssm_client: Any = None,
) -> CatalogSyncStats:
    """Fetch ``util/db`` per language, merge, and upsert only what changed.

    Two layers keep the weekly run cheap and fast:

    * **Checksum fast-path** -- when ``checksum_param`` is given and every
      configured language was fetched, a content checksum of the catalog is
      compared against the value stored in SSM; if unchanged, the run skips the
      scan and all writes.
    * **Diff writes** -- otherwise the current stored state is scanned and only
      new/changed items are upserted (the full catalog is written only on the
      first run or when everything genuinely changed).

    A failed/empty fetch yields no items for that language, so the sync is
    strictly additive and never deletes.
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
    checksum = catalog_checksum(merged)
    # Only trust / persist the checksum when the fetch was complete; a partial
    # fetch produces a digest that does not represent the true full catalog.
    complete_fetch = not failed

    ssm: Any = None
    if checksum_param is not None and complete_fetch:
        ssm = ssm_client if ssm_client is not None else boto3.client("ssm")
        if _read_checksum(checksum_param, ssm) == checksum:
            logger.info("catalog sync: checksum unchanged, skipping writes")
            return CatalogSyncStats(
                total=len(merged), new=0, langs=langs, fetched=fetched, written=0, unchanged=True
            )

    current = dynamo.scan_catalog_fingerprints()
    changed = diff_catalog(merged, current)
    _, new = dynamo.bulk_upsert_catalog_items(changed, max_workers=max_workers)

    if ssm is not None and checksum_param is not None:
        _write_checksum(checksum_param, checksum, ssm)

    logger.info(
        "catalog sync complete",
        extra={
            "total": len(merged),
            "written": len(changed),
            "new": new,
            "langs": langs,
            "fetched": fetched,
        },
    )
    return CatalogSyncStats(
        total=len(merged),
        new=new,
        langs=langs,
        fetched=fetched,
        written=len(changed),
        unchanged=False,
    )
