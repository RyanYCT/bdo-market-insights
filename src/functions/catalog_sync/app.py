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

from bdo_common import catalog, catalog_artifact
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

    # The catalog content checksum is stored co-located with the data, in the
    # items table's metadata row (ADR-0034) -- no external parameter to wire.
    stats = catalog.sync_catalog(
        ArshaClient(),
        langs,
        default_lang=DEFAULT_LANG,
        max_workers=max_workers,
    )

    failed_langs = [lang for lang, count in stats.fetched.items() if count == 0]
    metrics.add_metric(name="CatalogItemsSynced", unit=MetricUnit.Count, value=stats.total)
    metrics.add_metric(name="CatalogItemsWritten", unit=MetricUnit.Count, value=stats.written)
    metrics.add_metric(name="CatalogNewItems", unit=MetricUnit.Count, value=stats.new)
    # Non-zero when a language fetch failed (flaky util/db) or the run was
    # skipped because the default language failed -- alarm target.
    metrics.add_metric(
        name="CatalogLangFetchFailures", unit=MetricUnit.Count, value=len(failed_langs)
    )

    # Republish the full-catalog CDN artifact (ADR-0031). Skipped when the run
    # itself was skipped (default-language fetch failed, so the table state this
    # run reflects is untrustworthy) or when no artifact bucket is configured
    # (e.g. local invocation). Always republished otherwise -- including on the
    # checksum-unchanged fast-path -- so the artifact reflects current icon
    # materialization and is self-healing if a prior publish was missed.
    artifact_items = 0
    artifact_bucket = os.environ.get("CATALOG_ARTIFACT_BUCKET", "")
    if artifact_bucket and not stats.skipped:
        artifact_items = catalog_artifact.publish_catalog_artifact(
            bucket=artifact_bucket,
            icon_base=os.environ.get("ICON_BASE_URL", ""),
        )
        metrics.add_metric(
            name="CatalogArtifactItems", unit=MetricUnit.Count, value=artifact_items
        )

    logger.info(
        "catalogSync complete",
        extra={
            "total": stats.total,
            "written": stats.written,
            "new": stats.new,
            "langs": langs,
            "failed_langs": failed_langs,
            "skipped": stats.skipped,
            "unchanged": stats.unchanged,
            "artifact_items": artifact_items,
        },
    )
    return {
        "total": stats.total,
        "written": stats.written,
        "new": stats.new,
        "langs": langs,
        "failed_langs": failed_langs,
        "skipped": stats.skipped,
        "unchanged": stats.unchanged,
        "artifact_items": artifact_items,
    }
