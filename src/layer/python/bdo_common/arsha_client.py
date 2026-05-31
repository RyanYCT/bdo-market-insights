"""HTTP client and response normalizer for the arsha.io market API."""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import UTC, datetime
from typing import Any

from bdo_common.models import Record

logger = logging.getLogger(__name__)

_MAX_BATCH_SIZE = 50
_MAX_URL_LENGTH = 1900


def normalize_response(raw: dict[str, Any]) -> list[Record]:
    """Parse an arsha.io GetWorldMarketSubList response into Record objects.

    Handles all 5 polymorphic response shapes (single/multi item,
    enhanceable/non-enhanceable/mixed). Malformed rows are skipped with
    a warning log.
    """
    result_code = raw.get("resultCode")
    if result_code != 0:
        logger.warning("Non-zero resultCode: %s", result_code)
        return []

    result_msg = raw.get("resultMsg")
    if not result_msg or not isinstance(result_msg, str):
        return []

    records: list[Record] = []
    rows = result_msg.split("|")

    for row in rows:
        row = row.strip()
        if not row:
            continue
        try:
            parts = row.split("-")
            if len(parts) != 8:
                logger.warning("Malformed row (expected 8 fields, got %d): %s", len(parts), row)
                continue

            item_id = int(parts[0])
            sid = int(parts[1])
            base_price = int(parts[2])
            current_stock = int(parts[3])
            total_trades = int(parts[4])
            # parts[5] is base_price repeated -- skip
            last_sold_price = int(parts[6])
            last_sold_at_ts = int(parts[7])
            last_sold_at = datetime.fromtimestamp(last_sold_at_ts, tz=UTC)

            record = Record(
                item_id=item_id,
                sid=sid,
                base_price=base_price,
                current_stock=current_stock,
                total_trades=total_trades,
                last_sold_price=last_sold_price,
                last_sold_at=last_sold_at,
            )
            records.append(record)
        except (ValueError, TypeError) as exc:
            logger.warning("Failed to parse row %r: %s", row, exc)
            continue

    return records


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

    def fetch_sub_list(self, item_ids: list[int]) -> list[Record]:
        """Fetch market data for the given item IDs.

        Batches into groups of <= 50 IDs, further splitting if the URL
        exceeds 1900 characters. Network errors are logged and skipped.
        """
        if not item_ids:
            return []

        # Initial batching by max batch size
        batches: list[list[int]] = []
        for i in range(0, len(item_ids), _MAX_BATCH_SIZE):
            chunk = item_ids[i : i + _MAX_BATCH_SIZE]
            batches.extend(self._split_batch_by_url_length(chunk))

        all_records: list[Record] = []
        for batch in batches:
            url = self._build_url(batch)
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
                    data: dict[str, Any] = json.loads(resp.read().decode())
                records = normalize_response(data)
                all_records.extend(records)
            except Exception:
                logger.exception("Failed to fetch batch from %s", url)
                continue

        return all_records
