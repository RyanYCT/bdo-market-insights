"""catalogSync Lambda: refresh the full BDO item catalog from arsha ``util/db``.

Weekly EventBridge-triggered (scheduled after the BDO maintenance window so new
items are live). Fetches ``util/db`` for the configured languages, merges by id,
and partial-upserts every item into the items table (ETL-owned attributes
preserved, ADR-0018). Emits ``CatalogItemsSynced`` and ``CatalogNewItems``
metrics; newly discovered items surface via the latter for curation.
"""

from __future__ import annotations

import os
from typing import Any

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit

from bdo_common import catalog
from bdo_common.arsha_client import DEFAULT_LANG, ArshaClient

logger = Logger()
tracer = Tracer()
metrics = Metrics(namespace="BdoMarket")

_DEFAULT_LANGS = "en,tw"


def _langs() -> list[str]:
    """Parse the comma-separated CATALOG_LANGS env var (default en,tw)."""
    raw = os.environ.get("CATALOG_LANGS", _DEFAULT_LANGS)
    return [lang.strip() for lang in raw.split(",") if lang.strip()]


@metrics.log_metrics
@tracer.capture_lambda_handler
@logger.inject_lambda_context
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run one full-catalog sync and emit metrics."""
    langs = _langs()
    max_workers = int(os.environ.get("CATALOG_MAX_WORKERS", "16"))

    stats = catalog.sync_catalog(
        ArshaClient(), langs, default_lang=DEFAULT_LANG, max_workers=max_workers
    )

    metrics.add_metric(name="CatalogItemsSynced", unit=MetricUnit.Count, value=stats.total)
    metrics.add_metric(name="CatalogNewItems", unit=MetricUnit.Count, value=stats.new)
    logger.info(
        "catalogSync complete",
        extra={"total": stats.total, "new": stats.new, "langs": langs},
    )
    return {"total": stats.total, "new": stats.new, "langs": langs}
