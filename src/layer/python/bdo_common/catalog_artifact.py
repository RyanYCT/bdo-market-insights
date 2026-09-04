"""Publish the full item catalog as a static JSON artifact for the frontend.

The catalog is large, weekly-changing reference data (the full arsha ``util/db``
set, tens of thousands of items). Serving it through ``/v1/items`` would require
an unbounded DynamoDB scan per request, so instead ``catalogSync`` writes a
single ``catalog/catalog.json`` object into the icons bucket, delivered through
the same CloudFront CDN (ADR-0031). The frontend fetches the whole catalog in
one cached request and does client-side search/filter; ``/v1/items`` then serves
only the small, mutable tracked subset.

The object is stored as plain (uncompressed) JSON: CloudFront compresses it on
the wire (``Compress: true``), which keeps the stored object human-readable and
avoids pinning a ``Content-Encoding`` while still shipping compressed bytes to
the browser.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

import boto3

from bdo_common import dynamo
from bdo_common.icons import public_icon_url
from bdo_common.models import Item

logger = logging.getLogger(__name__)

#: Object key for the catalog artifact within the icons bucket. Served at
#: ``{IconBaseUrl}/catalog/catalog.json`` through the shared CloudFront CDN.
CATALOG_ARTIFACT_KEY = "catalog/catalog.json"

#: Browser cache lifetime for the artifact. The catalog only changes on the
#: weekly sync, so an hour keeps clients fresh without hammering the origin.
_CACHE_CONTROL = "public, max-age=3600"


def build_catalog_artifact(items: Iterable[Item], *, icon_base: str) -> list[dict[str, Any]]:
    """Project stored items onto the public catalog shape, sorted by id.

    Each entry carries ``{id, name, names, grade, category, main_category,
    sub_category, icon_url}`` -- the same public projection as the API's
    ``ItemResponse`` minus the mutable/internal fields (``tracked`` is served by
    the API, not baked into this reference artifact). ``icon_url`` is resolved
    against ``icon_base`` via the shared :func:`public_icon_url`.
    """
    return [
        {
            "id": item.id,
            "name": item.name,
            "names": item.names,
            "grade": item.grade,
            "category": item.category,
            "main_category": item.main_category,
            "sub_category": item.sub_category,
            "icon_url": public_icon_url(item.id, base=icon_base),
        }
        for item in sorted(items, key=lambda i: i.id)
    ]


def publish_catalog_artifact(
    *,
    bucket: str,
    icon_base: str,
    key: str = CATALOG_ARTIFACT_KEY,
    s3_client: Any = None,
) -> int:
    """Scan the items table, build the catalog artifact, and write it to S3.

    Returns the number of items in the published artifact. The object is written
    as plain UTF-8 JSON with a browser cache lifetime; CloudFront handles wire
    compression.
    """
    payload = build_catalog_artifact(dynamo.scan_catalog_items(), icon_base=icon_base)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    s3 = s3_client if s3_client is not None else boto3.client("s3")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json; charset=utf-8",
        CacheControl=_CACHE_CONTROL,
    )
    logger.info(
        "published catalog artifact",
        extra={"items": len(payload), "bytes": len(body), "bucket": bucket, "key": key},
    )
    return len(payload)
