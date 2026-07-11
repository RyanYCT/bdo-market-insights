"""Materialize BDO item icons from the official Pearl Abyss CDN into S3.

Icons are self-hosted rather than hotlinked. For each tracked item whose
``icon_status`` is ``unset``, fetch the PNG from the Pearl CDN and store it in
the icons bucket, then record the outcome on the item:

* ``stored``  -- fetched and written to S3.
* ``missing`` -- the CDN has no icon for this id (HTTP 403/404); stop retrying.
* left ``unset`` -- a transient/server error; retried on the next run.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import boto3

from bdo_common import dynamo
from bdo_common.models import Item

logger = logging.getLogger(__name__)

#: Pearl Abyss trade-market icon CDN host. The region segment is upper-cased
#: into the path, e.g. .../TW/TradeMarket/Common/img/BDO/item/<id>.png
ICON_SOURCE_BASE = "https://s1.pearlcdn.com"

#: S3 key prefix for stored icons (served later via a CDN).
ICON_KEY_PREFIX = "icons/"

#: A missing icon returns 403 (Forbidden) or 404 on the Pearl CDN; both mean
#: "no icon exists for this id", so the item is marked ``missing`` (not retried).
_MISSING_STATUS_CODES = (403, 404)


@dataclass(frozen=True)
class IconSyncStats:
    """Outcome of an icon materialization run."""

    stored: int = 0
    missing: int = 0
    errors: int = 0


def build_icon_url(item_id: int, *, region: str, base: str = ICON_SOURCE_BASE) -> str:
    """Build the Pearl CDN icon URL for an item id (region segment upper-cased)."""
    return f"{base.rstrip('/')}/{region.upper()}/TradeMarket/Common/img/BDO/item/{item_id}.png"


def fetch_icon(item_id: int, *, region: str, base: str = ICON_SOURCE_BASE) -> bytes | None:
    """Fetch an icon PNG, or ``None`` when the CDN reports it does not exist.

    Returns the image bytes on HTTP 200, ``None`` on 403/404 (no such icon), and
    re-raises any other error (5xx, network) so the caller leaves the item
    ``unset`` for a later retry.
    """
    url = build_icon_url(item_id, region=region, base=base)
    try:
        # URL is built internally from configured base + int id + region.
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310  # nosec B310
            data: bytes = resp.read()
        return data
    except urllib.error.HTTPError as exc:
        if exc.code in _MISSING_STATUS_CODES:
            return None
        raise


def sync_icons(
    items: Iterable[Item],
    *,
    bucket: str,
    region: str,
    base: str = ICON_SOURCE_BASE,
    s3_client: Any = None,
) -> IconSyncStats:
    """Materialize icons for ``items``; return counts of stored/missing/errors.

    Each item is processed independently: a transient failure on one item is
    logged and counted as an error (its ``icon_status`` is left unchanged so the
    next run retries it) and does not abort the rest.
    """
    s3 = s3_client if s3_client is not None else boto3.client("s3")
    stored = missing = errors = 0

    for item in items:
        try:
            data = fetch_icon(item.id, region=region, base=base)
            if data is None:
                dynamo.update_item(item.id, {"icon_status": "missing"})
                missing += 1
            else:
                s3.put_object(
                    Bucket=bucket,
                    Key=f"{ICON_KEY_PREFIX}{item.id}.png",
                    Body=data,
                    ContentType="image/png",
                    CacheControl="public, max-age=604800",
                )
                dynamo.update_item(item.id, {"icon_status": "stored"})
                stored += 1
        except Exception:
            logger.exception("icon materialization failed for item %s (left unset)", item.id)
            errors += 1

    return IconSyncStats(stored=stored, missing=missing, errors=errors)
