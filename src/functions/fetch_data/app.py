"""fetchData ETL Lambda: pull raw market data from arsha.io for one batch.

Receives one Map batch (``{region, snapshot_at, items}``), calls arsha.io
for the batch's item IDs, and attaches the raw (unparsed) payloads under
``raw``. Parsing is deferred to cleanData so retries here never re-parse and
retries there never re-hit the network (FR-3).

arsha proxies the Imperva-protected upstream market API and fails an entire
multi-id request if any single item is blocked. The fetch is therefore
resilient (adaptive bisect): individually blocked items are dropped and
reported rather than failing the whole batch. If too large a fraction of the
batch is unfetchable -- i.e. arsha is broadly down rather than a few items
being blocked -- the stage fails loudly so Step Functions surfaces it (and the
``ExecutionsFailed`` alarm fires) instead of the pipeline silently storing a
near-empty snapshot.
"""

from __future__ import annotations

from typing import Any

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit

from bdo_common.arsha_client import ArshaClient

logger = Logger()
tracer = Tracer()
metrics = Metrics(namespace="BdoMarket")

#: Fail the run when more than this fraction of a batch's items are unfetchable
#: after resilient retry + bisect. Below the threshold the run stores what it
#: got and skips the rest (the next hourly run picks them up); at or above it,
#: arsha is treated as broadly down and the stage fails loud.
_MAX_FAILED_ITEM_FRACTION = 0.2


@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Return the batch with raw arsha payloads attached under ``raw``."""
    region = event["region"]
    items = event["items"]
    item_ids = [int(item["id"]) for item in items]

    client = ArshaClient(region=region)
    payloads, failed_ids = client.fetch_raw_resilient(item_ids)

    metrics.add_metric(name="MarketItemsSkipped", unit=MetricUnit.Count, value=len(failed_ids))

    if item_ids and len(failed_ids) > _MAX_FAILED_ITEM_FRACTION * len(item_ids):
        logger.error(
            "fetchData failing: too many unfetchable items",
            extra={
                "region": region,
                "id_count": len(item_ids),
                "failed_count": len(failed_ids),
                "failed_ids": failed_ids,
            },
        )
        msg = (
            f"{len(failed_ids)}/{len(item_ids)} items unfetchable from arsha "
            "(upstream broadly blocked)"
        )
        raise RuntimeError(msg)

    logger.info(
        "fetchData complete",
        extra={
            "region": region,
            "id_count": len(item_ids),
            "payload_count": len(payloads),
            "failed_count": len(failed_ids),
            "failed_ids": failed_ids,
        },
    )
    return {**event, "raw": payloads}
