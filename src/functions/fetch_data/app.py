"""fetchData ETL Lambda: pull raw market data from arsha.io for one batch.

Receives one Map batch (``{region, snapshot_at, items}``), calls arsha.io
for the batch's item IDs, and attaches the raw (unparsed) payloads under
``raw``. Parsing is deferred to cleanData so retries here never re-parse and
retries there never re-hit the network (FR-3).
"""

from __future__ import annotations

from typing import Any

from aws_lambda_powertools import Logger, Tracer

from bdo_common.arsha_client import ArshaClient

logger = Logger()
tracer = Tracer()


@tracer.capture_lambda_handler
@logger.inject_lambda_context
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Return the batch with raw arsha payloads attached under ``raw``."""
    region = event["region"]
    items = event["items"]
    item_ids = [int(item["id"]) for item in items]

    client = ArshaClient(region=region)
    raw = client.fetch_raw(item_ids)

    logger.info(
        "fetchData complete",
        extra={"region": region, "id_count": len(item_ids), "payload_count": len(raw)},
    )
    return {**event, "raw": raw}
