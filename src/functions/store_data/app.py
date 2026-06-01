"""storeData ETL Lambda: persist one batch to RDS in a single transaction.

For one Map batch (``{region, snapshot_at, items, records}``) this upserts the
``item`` catalog rows (metadata from DynamoDB, ADR-0010) and the ``item_sid``
reference rows, then bulk-inserts ``market_snapshot`` rows -- all in one
transaction (FR-5). Snapshot inserts are idempotent on
``(region, item_id, sid, snapshot_at)`` so re-runs are safe (NFR-4). A failure
rolls the whole batch back and re-raises so Step Functions can retry/catch it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit

from bdo_common import db
from bdo_common.models import Record, SnapshotRow
from bdo_common.repositories import ItemRepo, ItemSidRepo, SnapshotRepo

logger = Logger()
tracer = Tracer()
metrics = Metrics(namespace="BdoMarket")


@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Upsert item + item_sid and bulk-insert snapshots for one batch."""
    region = event["region"]
    snapshot_at = datetime.fromisoformat(event["snapshot_at"])
    items = event["items"]
    records = [Record(**raw) for raw in event.get("records", [])]
    known_ids = {int(item["id"]) for item in items}

    conn = db.get_connection()
    try:
        for item in items:
            ItemRepo.upsert(
                conn,
                item_id=int(item["id"]),
                name=item["name"],
                category=item.get("category"),
                main_category=item.get("main_category"),
                sub_category=item.get("sub_category"),
            )

        snapshots: list[SnapshotRow] = []
        skipped = 0
        for rec in records:
            if rec.item_id not in known_ids:
                # A record for an item not in this batch's metadata would
                # violate the item_sid FK; skip rather than poison the batch.
                skipped += 1
                continue
            ItemSidRepo.upsert(
                conn,
                region=region,
                item_id=rec.item_id,
                sid=rec.sid,
                max_enhance=rec.max_enhance,
                price_min=rec.price_min,
                price_max=rec.price_max,
            )
            snapshots.append(
                SnapshotRow(
                    region=region,
                    snapshot_at=snapshot_at,
                    item_id=rec.item_id,
                    sid=rec.sid,
                    base_price=rec.base_price,
                    current_stock=rec.current_stock,
                    total_trades=rec.total_trades,
                    last_sold_price=rec.last_sold_price,
                    last_sold_at=rec.last_sold_at,
                )
            )

        snapshot_count = SnapshotRepo.bulk_insert(conn, snapshots)
        conn.commit()
    except Exception:
        conn.rollback()
        metrics.add_metric(name="EtlFailedItems", unit=MetricUnit.Count, value=len(items))
        logger.exception("storeData failed; transaction rolled back", extra={"region": region})
        raise

    metrics.add_metric(name="EtlSuccessfulItems", unit=MetricUnit.Count, value=len(items))
    logger.info(
        "storeData complete",
        extra={
            "region": region,
            "item_count": len(items),
            "sid_count": len(snapshots),
            "snapshot_count": snapshot_count,
            "skipped_records": skipped,
        },
    )
    return {
        "region": region,
        "item_count": len(items),
        "sid_count": len(snapshots),
        "snapshot_count": snapshot_count,
    }
