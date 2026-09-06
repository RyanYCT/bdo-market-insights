"""Unit test for the bdo_common.dynamo.catalog_is_empty guard helper."""

from __future__ import annotations

from typing import Any

import pytest


class _FakeTable:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items
        self.scan_kwargs: dict[str, Any] = {}

    def scan(self, **kwargs: Any) -> dict[str, Any]:
        self.scan_kwargs = kwargs
        return {"Items": self._items}


def test_catalog_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from bdo_common import dynamo

    empty = _FakeTable([])
    monkeypatch.setattr(dynamo, "_get_table", lambda: empty)
    assert dynamo.catalog_is_empty() is True
    # Cheap probe: Limit=2 (enough to see an entity row past the lone metadata
    # row), projects only the key.
    assert empty.scan_kwargs.get("Limit") == 2

    # A lone catalog-metadata row (reserved id 0, ADR-0034) is not a catalog
    # item, so the table still reads empty.
    monkeypatch.setattr(dynamo, "_get_table", lambda: _FakeTable([{"id": 0}]))
    assert dynamo.catalog_is_empty() is True

    # Any real entity row -> not empty (with or without the metadata row).
    monkeypatch.setattr(dynamo, "_get_table", lambda: _FakeTable([{"id": 1}]))
    assert dynamo.catalog_is_empty() is False
    monkeypatch.setattr(dynamo, "_get_table", lambda: _FakeTable([{"id": 0}, {"id": 1}]))
    assert dynamo.catalog_is_empty() is False
