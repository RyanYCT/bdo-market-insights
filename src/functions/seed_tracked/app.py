"""seedTracked Lambda: apply the committed tracked-item set to the items table.

A bootstrap-orchestrator task (ADR-0028). Marks the curated set ``tracked=true``
(plus the sparse tracked-index marker, ``category``/``main_category``/
``sub_category`` and ``cron_profile``) as a **partial ``UpdateItem``**, so the
catalog-owned fields (``name``/``grade``/``names``) that ``catalogSync`` populates
are preserved (ADR-0018).

Fully offline: reads the curated data files bundled next to this handler (copied
from ``scripts/data/`` at build time), never arsha. Idempotent -- re-running
re-applies the same partial updates. Additive only: it never untracks (the
destructive reconcile stays a deliberate local op via ``scripts/seed_items.py``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit

from bdo_common import dynamo, tracking

logger = Logger()
tracer = Tracer()
metrics = Metrics(namespace="BdoMarket")

_DATA_DIR = Path(__file__).resolve().parent / "data"


def _load(name: str) -> Any:
    """Load one bundled JSON data file by name."""
    with (_DATA_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def _category_map(categories: dict[str, Any]) -> dict[str, str]:
    """Reduce categories.json (``main:sub`` -> spec) to ``main:sub`` -> label."""
    return {
        key: str(spec["category"]) for key, spec in categories.items() if not key.startswith("_")
    }


@metrics.log_metrics
@tracer.capture_lambda_handler
@logger.inject_lambda_context
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Seed the curated tracked set into the items table; return counts."""
    entries: list[dict[str, Any]] = _load("tracked_items.json")
    catalog = tracking.parse_catalog(_load("full_items.json"))
    index = tracking.catalog_index(catalog)
    category_map = _category_map(_load("categories.json"))
    cron_by_id = tracking.cron_overrides(_load("track_sets.json"))

    plan: list[tuple[int, dict[str, Any]]] = []
    unclassified: list[int] = []
    for entry in entries:
        item_id = int(entry["id"])
        updates, classified = tracking.build_tracked_updates(
            item_id,
            series_profile=cron_by_id.get(item_id),
            index=index,
            category_map=category_map,
            model_id=entry.get("model_id"),
        )
        if not classified:
            unclassified.append(item_id)
        plan.append((item_id, updates))

    seeded = dynamo.bulk_update_items(plan)

    metrics.add_metric(name="TrackedItemsSeeded", unit=MetricUnit.Count, value=seeded)
    metrics.add_metric(
        name="TrackedItemsUnclassified", unit=MetricUnit.Count, value=len(unclassified)
    )
    logger.info(
        "seedTracked complete",
        extra={"seeded": seeded, "total": len(plan), "unclassified": unclassified},
    )
    return {"seeded": seeded, "total": len(plan), "unclassified": unclassified}
