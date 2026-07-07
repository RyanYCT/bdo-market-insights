"""HTTP client and response normalizer for the arsha.io market API."""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from bdo_common.models import Record

logger = logging.getLogger(__name__)

_MAX_BATCH_SIZE = 50
_MAX_URL_LENGTH = 1900

# arsha.io item dicts are identified by the presence of these keys. Anything
# else encountered while flattening (empty dicts, error envelopes) is ignored.
_IDENTITY_KEYS = ("id", "sid")

#: arsha.io ``lang`` query-param codes -> human-readable label. Used by the
#: item-catalog sync (``util/db?lang=``) and any language-aware request. The
#: ``grade`` returned by ``util/db`` is language-independent; ``lang`` only
#: changes the localized item ``name``.
SUPPORTED_LANGS: dict[str, str] = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "ru": "Russian",
    "es": "Spanish (EU)",
    "sp": "Portuguese (RedFox)",
    "pt": "Portuguese",
    "jp": "Japanese",
    "kr": "Korean",
    "th": "Thai",
    "tr": "Turkish",
    "tw": "Chinese (Taiwan)",
    "cn": "Chinese (Mainland)",
}

#: Default language when a caller omits ``lang`` (matches the project default).
DEFAULT_LANG = "en"


def _iter_item_dicts(node: Any) -> Iterator[dict[str, Any]]:
    """Recursively yield item dicts from arsha's polymorphic JSON.

    arsha.io returns one of several shapes depending on how many items and
    enhancement levels are requested:

    * a single object              -> one non-enhanceable item
    * a list of objects            -> one enhanceable item, or many sid=0 items
    * a list of lists of objects   -> many enhanceable items
    * any mixture of the above

    Walking the structure recursively flattens every shape: dicts are item
    rows, lists are containers to descend into, scalars are ignored.
    """
    if isinstance(node, dict):
        yield node
    elif isinstance(node, list):
        for element in node:
            yield from _iter_item_dicts(element)


def _parse_record(obj: dict[str, Any]) -> Record | None:
    """Map a single arsha.io item dict onto a Record, or None if not parseable.

    Dicts that lack the identity keys (e.g. ``{}`` or an error envelope) are
    not item rows and return None silently. Item rows that are present but
    malformed are skipped with a warning.
    """
    if not all(key in obj for key in _IDENTITY_KEYS):
        return None
    try:
        return Record(
            item_id=int(obj["id"]),
            sid=int(obj["sid"]),
            name=str(obj["name"]),
            base_price=int(obj["basePrice"]),
            current_stock=int(obj["currentStock"]),
            total_trades=int(obj["totalTrades"]),
            last_sold_price=int(obj["lastSoldPrice"]),
            last_sold_at=datetime.fromtimestamp(int(obj["lastSoldTime"]), tz=UTC),
            max_enhance=int(obj["maxEnhance"]),
            price_min=int(obj["priceMin"]),
            price_max=int(obj["priceMax"]),
        )
    except (KeyError, ValueError, TypeError, OverflowError, OSError) as exc:
        logger.warning("Skipping malformed arsha item %r: %s", obj, exc)
        return None


def normalize_response(raw: Any) -> list[Record]:
    """Flatten an arsha.io GetWorldMarketSubList response into Record objects.

    Handles all polymorphic shapes (single/multi item,
    enhanceable/non-enhanceable, and mixed) by recursively flattening the JSON
    into item dicts. Non-item dicts and malformed rows are skipped.
    """
    return [record for obj in _iter_item_dicts(raw) if (record := _parse_record(obj)) is not None]


class ArshaClient:
    """HTTP client for the arsha.io market data API."""

    def __init__(
        self,
        *,
        base_url: str = "https://api.arsha.io/v2",
        region: str = "tw",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._region = region

    def _build_url(self, ids: list[int]) -> str:
        """Build the GetWorldMarketSubList URL for a batch of item IDs."""
        csv_ids = ",".join(str(i) for i in ids)
        return f"{self._base_url}/{self._region}/GetWorldMarketSubList?id={csv_ids}"

    def _split_batch_by_url_length(self, ids: list[int]) -> list[list[int]]:
        """Split a batch further if the resulting URL exceeds the max length."""
        url = self._build_url(ids)
        if len(url) <= _MAX_URL_LENGTH:
            return [ids]

        mid = len(ids) // 2
        left = ids[:mid]
        right = ids[mid:]

        result: list[list[int]] = []
        if left:
            result.extend(self._split_batch_by_url_length(left))
        if right:
            result.extend(self._split_batch_by_url_length(right))
        return result

    def _plan_batches(self, item_ids: list[int]) -> list[list[int]]:
        """Group IDs into <= 50-ID batches, splitting any over-long URL."""
        batches: list[list[int]] = []
        for i in range(0, len(item_ids), _MAX_BATCH_SIZE):
            chunk = item_ids[i : i + _MAX_BATCH_SIZE]
            batches.extend(self._split_batch_by_url_length(chunk))
        return batches

    def fetch_raw(self, item_ids: list[int]) -> list[Any]:
        """Fetch raw arsha.io JSON payloads (one per HTTP request), unparsed.

        Batches into groups of <= 50 IDs, further splitting if the URL
        exceeds 1900 characters. Network errors are logged and skipped.
        Used by the ETL ``fetchData`` Lambda so that parsing happens in a
        separate ``cleanData`` stage (retries never re-hit the network).
        """
        if not item_ids:
            return []

        payloads: list[Any] = []
        for batch in self._plan_batches(item_ids):
            url = self._build_url(batch)
            try:
                # URL is built internally and is always https://api.arsha.io/...
                with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310  # nosec B310
                    payloads.append(json.loads(resp.read().decode()))
            except Exception:
                logger.exception("Failed to fetch batch from %s", url)
                continue
        return payloads

    def fetch_sub_list(self, item_ids: list[int]) -> list[Record]:
        """Fetch and normalize market data for the given item IDs.

        Convenience wrapper over :meth:`fetch_raw` + :func:`normalize_response`
        for callers that want parsed records in one call.
        """
        all_records: list[Record] = []
        for payload in self.fetch_raw(item_ids):
            all_records.extend(normalize_response(payload))
        return all_records
