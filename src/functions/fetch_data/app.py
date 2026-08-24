"""fetchData ETL Lambda: pull raw market data from arsha.io for one batch.

Receives one Map batch (``{region, snapshot_at, items}``), calls arsha.io
for the batch's item IDs, and attaches the raw (unparsed) payloads under
``raw``. Parsing is deferred to cleanData so retries here never re-parse and
retries there never re-hit the network (FR-3).

arsha proxies the Imperva-protected upstream market API and fails an entire
multi-id request if any single item is blocked. The fetch is therefore
resilient (adaptive bisect): individually blocked items are dropped and
reported rather than failing the whole batch, and whatever was fetched is
passed downstream to be stored -- a dropped item simply has no snapshot for
this hour (surfaced by the ``MarketItemsSkipped`` metric and, if the drops
persist, the sustained skip alarm). The stage fails loud only when *nothing*
could be fetched (arsha broadly down): there is no partial data to preserve and
it warrants immediate visibility via the ``ExecutionsFailed`` alarm.
"""

from __future__ import annotations

from typing import Any

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit

from bdo_common.arsha_client import ArshaClient

logger = Logger()
tracer = Tracer()
metrics = Metrics(namespace="BdoMarket")


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

    # Fail loud only when nothing at all could be fetched for a non-empty batch
    # (arsha broadly down): no partial data to preserve, so surface it via the
    # ExecutionsFailed alarm. A partial fetch stores what it got; a transient bad
    # window self-heals next run, and sustained drops trip the skip alarm.
    if item_ids and not payloads:
        logger.error(
            "fetchData failing: no items could be fetched",
            extra={"region": region, "id_count": len(item_ids), "failed_count": len(failed_ids)},
        )
        msg = f"all {len(item_ids)} items unfetchable from arsha (upstream down)"
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
