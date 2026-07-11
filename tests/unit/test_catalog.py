"""Tests for bdo_common.catalog (merge + sync orchestration)."""

from __future__ import annotations

from typing import Any

import pytest

from bdo_common import catalog, dynamo
from bdo_common.models import CatalogEntry, MergedCatalogItem


class _StubClient:
    """Stand-in for ArshaClient.fetch_item_db returning canned entries per lang."""

    def __init__(self, by_lang: dict[str, list[CatalogEntry]]) -> None:
        self._by_lang = by_lang
        self.calls: list[str] = []

    def fetch_item_db(self, lang: str) -> list[CatalogEntry]:
        self.calls.append(lang)
        return self._by_lang.get(lang, [])


class TestMergeCatalog:
    """merge_catalog: English canonical name, localized names, grade fallback."""

    def test_english_canonical_with_localized_names(self) -> None:
        by_lang = {
            "en": [CatalogEntry(item_id=1, name="Wild Herb", grade=4)],
            "tw": [CatalogEntry(item_id=1, name="野生藥草", grade=4)],
        }
        merged = catalog.merge_catalog(by_lang, default_lang="en")
        assert merged == [
            MergedCatalogItem(item_id=1, name="Wild Herb", names={"tw": "野生藥草"}, grade=4)
        ]

    def test_missing_default_lang_falls_back(self) -> None:
        by_lang = {
            "en": [],
            "tw": [CatalogEntry(item_id=2, name="只有中文", grade=2)],
        }
        (merged,) = catalog.merge_catalog(by_lang, default_lang="en")
        assert merged.item_id == 2
        assert merged.name == "只有中文"  # fell back to tw for the canonical name
        assert merged.names == {"tw": "只有中文"}
        assert merged.grade == 2

    def test_grade_zero_and_none_preserved(self) -> None:
        by_lang = {
            "en": [
                CatalogEntry(item_id=3, name="White", grade=0),
                CatalogEntry(item_id=4, name="NoGrade", grade=None),
            ]
        }
        by_id = {m.item_id: m for m in catalog.merge_catalog(by_lang)}
        assert by_id[3].grade == 0
        assert by_id[4].grade is None

    def test_ids_unioned_and_sorted(self) -> None:
        by_lang = {
            "en": [CatalogEntry(item_id=5, name="E5", grade=1)],
            "tw": [CatalogEntry(item_id=1, name="T1", grade=1)],
        }
        merged = catalog.merge_catalog(by_lang, default_lang="en")
        assert [m.item_id for m in merged] == [1, 5]


class TestSyncCatalog:
    """sync_catalog: fetch each lang, merge, delegate to bulk upsert."""

    def test_fetches_merges_and_upserts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        by_lang = {
            "en": [
                CatalogEntry(item_id=1, name="A", grade=4),
                CatalogEntry(item_id=2, name="B", grade=3),
            ],
            "tw": [CatalogEntry(item_id=1, name="甲", grade=4)],
        }
        client = _StubClient(by_lang)
        captured: dict[str, Any] = {}

        def fake_bulk(items: Any, *, max_workers: int = 16) -> tuple[int, int]:
            item_list = list(items)
            captured["items"] = item_list
            captured["max_workers"] = max_workers
            return (len(item_list), len(item_list))

        monkeypatch.setattr(dynamo, "bulk_upsert_catalog_items", fake_bulk)

        stats = catalog.sync_catalog(client, ["en", "tw"], max_workers=8)  # type: ignore[arg-type]

        assert client.calls == ["en", "tw"]
        assert stats.total == 2
        assert stats.new == 2
        assert stats.langs == ["en", "tw"]
        assert captured["max_workers"] == 8
        by_id = {m.item_id: m for m in captured["items"]}
        assert by_id[1].names == {"tw": "甲"}
        assert by_id[2].names == {}  # id 2 only in en

    def test_skips_when_default_lang_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # en (canonical) failed to fetch -> empty; tw succeeded.
        client = _StubClient({"en": [], "tw": [CatalogEntry(item_id=1, name="甲", grade=4)]})
        called = {"bulk": False}

        def fake_bulk(items: Any, *, max_workers: int = 16) -> tuple[int, int]:
            called["bulk"] = True
            return (0, 0)

        monkeypatch.setattr(dynamo, "bulk_upsert_catalog_items", fake_bulk)

        stats = catalog.sync_catalog(client, ["en", "tw"])  # type: ignore[arg-type]

        assert stats.skipped is True
        assert stats.total == 0
        assert stats.fetched == {"en": 0, "tw": 1}
        assert called["bulk"] is False  # no write when the canonical language failed

    def test_proceeds_when_nondefault_lang_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # tw failed -> empty; en fine. Merge writes en names only; empty tw is
        # not written, so any existing localized names are preserved.
        client = _StubClient({"en": [CatalogEntry(item_id=1, name="A", grade=4)], "tw": []})
        captured: dict[str, Any] = {}

        def fake_bulk(items: Any, *, max_workers: int = 16) -> tuple[int, int]:
            item_list = list(items)
            captured["items"] = item_list
            return (len(item_list), len(item_list))

        monkeypatch.setattr(dynamo, "bulk_upsert_catalog_items", fake_bulk)

        stats = catalog.sync_catalog(client, ["en", "tw"])  # type: ignore[arg-type]

        assert stats.skipped is False
        assert stats.fetched == {"en": 1, "tw": 0}
        assert captured["items"][0].names == {}  # no tw this run
