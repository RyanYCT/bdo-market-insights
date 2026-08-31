"""iconOrigin: CloudFront origin-failover Lambda for read-through icon delivery.

The delivery distribution's icons behavior uses an origin group: the S3 bucket
is the primary origin, this Lambda (a Function URL, OAC-signed) is the secondary.
CloudFront invokes this Lambda **only when S3 returns 403/404** — i.e. the icon
has not been materialized yet. It fetches the icon from the Pearl Abyss CDN,
stores it into the delivery bucket (so every subsequent request is served
straight from S3), and returns the bytes to CloudFront for this request. Icons
thus materialize on first view and self-heal if the bucket is ever recreated;
no ``icon_status`` bookkeeping or backfill is involved (ADR-0023, ADR-0033).

Intentionally standard-library + boto3 only (both in the Lambda runtime), with
no shared layer: it keeps the ``cdn`` stack free of a platform-layer dependency
and minimises cold-start and import-failure surface on the origin path.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any

import boto3

logger = logging.getLogger("icon_origin")
logger.setLevel(logging.INFO)

#: Pearl Abyss trade-market icon CDN; region segment is upper-cased into the
#: path (mirrors bdo_common.icons.build_icon_url, kept inline to stay layer-free).
_PEARL_BASE = "https://s1.pearlcdn.com"
_KEY_PREFIX = "icons/"
_PATH_RE = re.compile(r"^/icons/(\d+)\.png$")
#: A missing icon returns 403/404 upstream; both mean "no icon for this id".
_MISSING_STATUS = (403, 404)
_CACHE_CONTROL = "public, max-age=604800"


def _pearl_url(item_id: int, region: str) -> str:
    return f"{_PEARL_BASE}/{region.upper()}/TradeMarket/Common/img/BDO/item/{item_id}.png"


def _response(
    status: int, *, body: bytes = b"", content_type: str = "text/plain"
) -> dict[str, Any]:
    """Build a Lambda Function URL response (binary bodies are base64-encoded)."""
    headers = {"content-type": content_type}
    if status == 200:
        headers["cache-control"] = _CACHE_CONTROL
    return {
        "statusCode": status,
        "headers": headers,
        "body": base64.b64encode(body).decode() if body else "",
        "isBase64Encoded": True,
    }


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Materialize one icon on an S3 miss, then return its bytes."""
    path = event.get("rawPath") or event.get("requestContext", {}).get("http", {}).get("path", "")
    match = _PATH_RE.match(path or "")
    if match is None:
        return _response(404)
    item_id = int(match.group(1))
    region = os.environ.get("BDO_REGION", "tw")
    bucket = os.environ["ICONS_BUCKET"]

    try:
        # URL is built internally from a fixed base + int id + configured region.
        with urllib.request.urlopen(  # noqa: S310  # nosec B310
            _pearl_url(item_id, region), timeout=15
        ) as resp:
            data: bytes = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in _MISSING_STATUS:
            logger.info("no upstream icon for %s (%s)", item_id, exc.code)
            return _response(404)
        logger.exception("upstream error fetching icon %s", item_id)
        return _response(502)
    except Exception:
        logger.exception("failed fetching icon %s", item_id)
        return _response(502)

    try:
        boto3.client("s3").put_object(
            Bucket=bucket,
            Key=f"{_KEY_PREFIX}{item_id}.png",
            Body=data,
            ContentType="image/png",
            CacheControl=_CACHE_CONTROL,
        )
    except Exception:
        # Best-effort store: still return the bytes so the viewer gets the icon;
        # a later request will retry the store.
        logger.exception("failed storing icon %s to %s", item_id, bucket)

    return _response(200, body=data, content_type="image/png")
