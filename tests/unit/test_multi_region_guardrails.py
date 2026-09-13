"""Regression guardrails for multi-region-readiness (Req 4).

The feature is confined to the trigger layer plus a discovery endpoint. These
tests fail if a later change crosses the preserved boundaries: tracking must
stay global (one ``tracked`` boolean and one ``tracked-index`` GSI), ``/v1/items``
must stay region-agnostic (item identity is global), and neither the DynamoDB
model nor the RDS schema may change (no new migration).
"""

from __future__ import annotations

import pathlib

import yaml

from bdo_common import dynamo
from bdo_common.models import Item

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


class TestTrackingStaysGlobal:
    """Req 4.1: one global tracked boolean + one tracked-index GSI."""

    def test_item_tracked_is_a_single_bool(self) -> None:
        # A per-region tracking design would change this away from a plain bool.
        assert Item.model_fields["tracked"].annotation is bool

    def test_single_sparse_tracked_index(self) -> None:
        assert dynamo._TRACKED_GSI_NAME == "tracked-index"
        assert dynamo._TRACKED_MARKER_ATTR == "t"


class TestItemsRegionAgnostic:
    """Req 4.2: item identity is global; /v1/items takes no region."""

    def test_item_model_has_no_region_field(self) -> None:
        assert "region" not in Item.model_fields

    def test_items_routes_declare_no_region_parameter(self) -> None:
        spec = yaml.safe_load((_REPO_ROOT / "infra" / "openapi.yaml").read_text())
        for path, operations in spec["paths"].items():
            if not path.startswith("/v1/items"):
                continue
            for method, operation in operations.items():
                if method not in _HTTP_METHODS:
                    continue
                names = {param["name"] for param in operation.get("parameters", [])}
                assert "region" not in names, f"{method.upper()} {path} exposes a region param"


class TestSchemaUnchanged:
    """Req 4.3: the feature adds no migration (RDS schema untouched)."""

    def test_migration_set_is_unchanged(self) -> None:
        versions = _REPO_ROOT / "migrations" / "versions"
        revisions = sorted(p.name for p in versions.glob("*.py"))
        assert revisions == [
            "0001_initial.py",
            "0002_bootstrap_roles.py",
            "0003_migrator_role.py",
            "0004_market_summary.py",
        ]
