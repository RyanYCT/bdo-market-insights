"""Tests for the co-located catalog checksum metadata row (ADR-0034).

The catalog content checksum lives as a reserved row (id 0) in the items table
so it shares the table's lifecycle. These tests cover the read/write helpers and
verify the reserved row is excluded from the paths that enumerate the catalog.
"""

from __future__ import annotations

from typing import Any

import pytest

from bdo_common import dynamo


class _FakeTable:
    """Minimal Table double: canned get_item/scan and captured update_item."""

    def __init__(
        self,
        *,
        item: dict[str, Any] | None = None,
        scan_items: list[dict[str, Any]] | None = None,
    ) -> None:
        self._item = item
        self._scan_items = scan_items or []
        self.update_kwargs: dict[str, Any] = {}
        self.get_key: dict[str, Any] | None = None

    def get_item(self, *, Key: dict[str, Any]) -> dict[str, Any]:  # noqa: N803 (boto3 kwarg)
        self.get_key = Key
        return {"Item": self._item} if self._item is not None else {}

    def update_item(self, **kwargs: Any) -> dict[str, Any]:
        self.update_kwargs = kwargs
        return {}

    def scan(self, **kwargs: Any) -> dict[str, Any]:
        return {"Items": self._scan_items}


class TestReadCatalogChecksum:
    def test_returns_value_when_row_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        table = _FakeTable(item={"id": 0, "checksum": "abc123", "kind": "catalog-meta"})
        monkeypatch.setattr(dynamo, "_get_table", lambda: table)
        assert dynamo.read_catalog_checksum() == "abc123"
        assert table.get_key == {"id": dynamo._CATALOG_META_ID}

    def test_returns_none_when_row_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dynamo, "_get_table", lambda: _FakeTable(item=None))
        assert dynamo.read_catalog_checksum() is None

    def test_returns_none_when_attribute_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Row exists but carries no checksum attribute -> treated as unset.
        monkeypatch.setattr(dynamo, "_get_table", lambda: _FakeTable(item={"id": 0}))
        assert dynamo.read_catalog_checksum() is None


class TestWriteCatalogChecksum:
    def test_writes_reserved_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        table = _FakeTable()
        monkeypatch.setattr(dynamo, "_get_table", lambda: table)

        dynamo.write_catalog_checksum("deadbeef")

        assert table.update_kwargs["Key"] == {"id": dynamo._CATALOG_META_ID}
        # The reserved word `checksum` is aliased; the value is bound.
        assert table.update_kwargs["ExpressionAttributeValues"][":c"] == "deadbeef"
        assert table.update_kwargs["ExpressionAttributeNames"]["#checksum"] == "checksum"


class TestSentinelExcludedFromScans:
    def test_scan_catalog_items_excludes_metadata_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        table = _FakeTable(
            scan_items=[
                {"id": 0, "checksum": "abc", "kind": "catalog-meta"},  # reserved row
                {"id": 1, "name": "Silver", "grade": 0},
            ]
        )
        monkeypatch.setattr(dynamo, "_get_table", lambda: table)
        items = dynamo.scan_catalog_items()
        assert [i.id for i in items] == [1]  # metadata row not in the artifact

    def test_scan_catalog_fingerprints_excludes_metadata_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        table = _FakeTable(
            scan_items=[
                {"id": 0, "checksum": "abc"},  # reserved row (no name/grade)
                {"id": 7, "name": "A", "grade": 3, "names": {"tw": "甲"}},
            ]
        )
        monkeypatch.setattr(dynamo, "_get_table", lambda: table)
        fingerprints = dynamo.scan_catalog_fingerprints()
        assert set(fingerprints) == {7}  # id 0 excluded

    def test_get_item_returns_none_for_reserved_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # get_item short-circuits before touching the table for the reserved id.
        def _boom() -> Any:  # pragma: no cover - must not be called
            raise AssertionError("table should not be read for the reserved id")

        monkeypatch.setattr(dynamo, "_get_table", _boom)
        assert dynamo.get_item(dynamo._CATALOG_META_ID) is None
