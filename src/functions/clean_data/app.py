"""cleanData ETL Lambda: normalize raw arsha payloads into Records.

Flattens the raw payloads attached by fetchData into a list of normalized
records (FR-4) and drops the bulky ``raw`` blob from the state. Pure parsing,
no network or DB access.
"""

from __future__ import annotations

from typing import Any

from aws_lambda_powertools import Logger, Tracer

from bdo_common.arsha_client import normalize_response
from bdo_common.models import Record

logger = Logger()
tracer = Tracer()


@tracer.capture_lambda_handler
@logger.inject_lambda_context
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Return ``{region, snapshot_at, items, records}`` with ``raw`` dropped."""
    records: list[Record] = []
    for payload in event.get("raw", []):
        records.extend(normalize_response(payload))

    record_dicts = [r.model_dump(mode="json") for r in records]
    logger.info("cleanData complete", extra={"record_count": len(record_dicts)})
    return {
        "region": event["region"],
        "snapshot_at": event["snapshot_at"],
        "items": event["items"],
        "records": record_dicts,
    }
