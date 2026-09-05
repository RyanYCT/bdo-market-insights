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
        monkeypatch.setattr(dynamo, "scan_catalog_fingerprints", dict)

        # use_checksum=False isolates the merge/diff/upsert path from the
        # checksum fast-path (which is exercised in TestSyncCatalogChecksum).
        stats = catalog.sync_catalog(
            client,  # type: ignore[arg-type]
            ["en", "tw"],
            max_workers=8,
            use_checksum=False,
        )

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
        monkeypatch.setattr(dynamo, "scan_catalog_fingerprints", dict)

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
        monkeypatch.setattr(dynamo, "scan_catalog_fingerprints", dict)

        stats = catalog.sync_catalog(client, ["en", "tw"])  # type: ignore[arg-type]

        assert stats.skipped is False
        assert stats.fetched == {"en": 1, "tw": 0}
        assert captured["items"][0].names == {}  # no tw this run


class TestCatalogChecksum:
    """catalog_checksum: stable, order-independent, change-sensitive."""

    def _items(self) -> list[MergedCatalogItem]:
        return [
            MergedCatalogItem(item_id=1, name="A", names={"tw": "甲"}, grade=4),
            MergedCatalogItem(item_id=2, name="B", names={}, grade=0),
        ]

    def test_stable_and_order_independent(self) -> None:
        items = self._items()
        assert catalog.catalog_checksum(items) == catalog.catalog_checksum(list(reversed(items)))

    def test_changes_on_rename(self) -> None:
        base = catalog.catalog_checksum(self._items())
        renamed = self._items()
        renamed[0] = MergedCatalogItem(item_id=1, name="A2", names={"tw": "甲"}, grade=4)
        assert catalog.catalog_checksum(renamed) != base

    def test_changes_on_regrade_localization_and_addremove(self) -> None:
        base = catalog.catalog_checksum(self._items())
        regraded = [
            MergedCatalogItem(item_id=1, name="A", names={"tw": "甲"}, grade=5)
        ] + self._items()[1:]
        relocalized = [
            MergedCatalogItem(item_id=1, name="A", names={"tw": "乙"}, grade=4)
        ] + self._items()[1:]
        added = [*self._items(), MergedCatalogItem(item_id=3, name="C", grade=1)]
        assert catalog.catalog_checksum(regraded) != base
        assert catalog.catalog_checksum(relocalized) != base
        assert catalog.catalog_checksum(added) != base


class TestDiffCatalog:
    """diff_catalog: only new/changed items; empty names never flag a change."""

    def test_new_changed_and_unchanged(self) -> None:
        merged = [
            MergedCatalogItem(item_id=1, name="A", names={"tw": "甲"}, grade=4),  # unchanged
            MergedCatalogItem(item_id=2, name="B2", names={"tw": "乙"}, grade=3),  # name changed
            MergedCatalogItem(item_id=3, name="C", names={"tw": "丙"}, grade=2),  # regrade
            MergedCatalogItem(item_id=4, name="D", names={"tw": "丁"}, grade=1),  # localization
            MergedCatalogItem(item_id=5, name="E", names={"tw": "戊"}, grade=1),  # new
        ]
        current: dict[int, tuple[str, int | None, dict[str, str]]] = {
            1: ("A", 4, {"tw": "甲"}),
            2: ("B", 3, {"tw": "乙"}),
            3: ("C", 9, {"tw": "丙"}),
            4: ("D", 1, {"tw": "OLD"}),
        }
        changed = {m.item_id for m in catalog.diff_catalog(merged, current)}
        assert changed == {2, 3, 4, 5}

    def test_empty_names_not_flagged(self) -> None:
        # merged lost tw (fetch failed) but name/grade unchanged -> not written,
        # so the existing localized name is preserved.
        merged = [MergedCatalogItem(item_id=1, name="A", names={}, grade=4)]
        current: dict[int, tuple[str, int | None, dict[str, str]]] = {1: ("A", 4, {"tw": "甲"})}
        assert catalog.diff_catalog(merged, current) == []


class TestSyncCatalogChecksum:
    """sync_catalog checksum fast-path + diff + DynamoDB checksum persistence (ADR-0034)."""

    def _client(self) -> _StubClient:
        return _StubClient(
            {
                "en": [CatalogEntry(item_id=1, name="A", grade=4)],
                "tw": [CatalogEntry(item_id=1, name="甲", grade=4)],
            }
        )

    def _current_checksum(self) -> str:
        """Checksum of the catalog the stub client returns (for the match cases)."""
        return catalog.catalog_checksum(
            catalog.merge_catalog(
                {
                    "en": [CatalogEntry(item_id=1, name="A", grade=4)],
                    "tw": [CatalogEntry(item_id=1, name="甲", grade=4)],
                }
            )
        )

    @staticmethod
    def _capture_writes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
        """Patch dynamo.write_catalog_checksum to record persisted checksums."""
        writes: list[str] = []
        monkeypatch.setattr(dynamo, "write_catalog_checksum", writes.append)
        return writes

    def test_skips_writes_when_checksum_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._client()
        writes = self._capture_writes(monkeypatch)
        called = {"scan": False, "bulk": False}

        def mark_scan() -> dict[int, Any]:
            called["scan"] = True
            return {}

        def mark_bulk(items: Any, **_: Any) -> tuple[int, int]:
            called["bulk"] = True
            return (0, 0)

        monkeypatch.setattr(dynamo, "read_catalog_checksum", self._current_checksum)
        monkeypatch.setattr(dynamo, "scan_catalog_fingerprints", mark_scan)
        monkeypatch.setattr(dynamo, "bulk_upsert_catalog_items", mark_bulk)
        monkeypatch.setattr(dynamo, "catalog_is_empty", lambda: False)  # table holds the catalog

        stats = catalog.sync_catalog(client, ["en", "tw"])  # type: ignore[arg-type]

        assert stats.unchanged is True
        assert stats.written == 0
        assert stats.total == 1
        assert called == {"scan": False, "bulk": False}  # skipped entirely
        assert writes == []  # nothing re-written

    def test_writes_when_checksum_matches_but_table_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Defence-in-depth guard: even with the checksum co-located in the table
        # (so a recreate normally drops it too), if a matching checksum row
        # somehow survives while the entity rows are gone, the empty-table guard
        # must still force the full write rather than trust the checksum and skip.
        client = self._client()
        writes = self._capture_writes(monkeypatch)
        captured: dict[str, Any] = {}

        def fake_bulk(items: Any, *, max_workers: int = 16) -> tuple[int, int]:
            captured["ids"] = [m.item_id for m in items]
            return (len(captured["ids"]), len(captured["ids"]))

        monkeypatch.setattr(dynamo, "read_catalog_checksum", self._current_checksum)  # matches
        monkeypatch.setattr(dynamo, "scan_catalog_fingerprints", dict)  # empty table
        monkeypatch.setattr(dynamo, "bulk_upsert_catalog_items", fake_bulk)
        monkeypatch.setattr(dynamo, "catalog_is_empty", lambda: True)

        stats = catalog.sync_catalog(client, ["en", "tw"])  # type: ignore[arg-type]

        assert stats.unchanged is False  # did not skip despite the matching checksum
        assert stats.written == 1
        assert captured["ids"] == [1]  # the full catalog was written
        assert writes == [self._current_checksum()]  # checksum re-persisted

    def test_empty_check_skipped_on_checksum_mismatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The empty-table guard is gated behind the checksum comparison via
        # short-circuit, so a changed catalog (the common weekly path) must never
        # pay for the extra Scan. Pin that: catalog_is_empty() is not consulted
        # when the stored checksum does not match.
        client = self._client()
        self._capture_writes(monkeypatch)
        monkeypatch.setattr(dynamo, "read_catalog_checksum", lambda: "stale-checksum")
        monkeypatch.setattr(dynamo, "scan_catalog_fingerprints", dict)  # empty table
        monkeypatch.setattr(
            dynamo, "bulk_upsert_catalog_items", lambda items, **k: (len(list(items)), 1)
        )
        empty_checked = {"called": False}

        def spy_is_empty() -> bool:
            empty_checked["called"] = True
            return True

        monkeypatch.setattr(dynamo, "catalog_is_empty", spy_is_empty)

        stats = catalog.sync_catalog(client, ["en", "tw"])  # type: ignore[arg-type]

        assert empty_checked["called"] is False  # short-circuited before the scan
        assert stats.unchanged is False  # mismatch still drives a write

    def test_writes_only_changed_and_stores_checksum(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _StubClient(
            {
                "en": [
                    CatalogEntry(item_id=1, name="A", grade=4),
                    CatalogEntry(item_id=2, name="B", grade=3),
                ],
                "tw": [
                    CatalogEntry(item_id=1, name="甲", grade=4),
                    CatalogEntry(item_id=2, name="乙", grade=3),
                ],
            }
        )
        # id 1 unchanged, id 2 name differs.
        current: dict[int, tuple[str, int | None, dict[str, str]]] = {
            1: ("A", 4, {"tw": "甲"}),
            2: ("B-old", 3, {"tw": "乙"}),
        }
        monkeypatch.setattr(dynamo, "scan_catalog_fingerprints", lambda: current)
        captured: dict[str, Any] = {}

        def fake_bulk(items: Any, *, max_workers: int = 16) -> tuple[int, int]:
            captured["ids"] = [m.item_id for m in items]
            return (len(captured["ids"]), 0)

        monkeypatch.setattr(dynamo, "bulk_upsert_catalog_items", fake_bulk)
        monkeypatch.setattr(dynamo, "read_catalog_checksum", lambda: "stale-checksum")
        writes = self._capture_writes(monkeypatch)

        stats = catalog.sync_catalog(client, ["en", "tw"])  # type: ignore[arg-type]

        assert captured["ids"] == [2]  # only the changed item written
        assert stats.written == 1
        assert stats.total == 2
        assert len(writes) == 1  # new checksum persisted

    def test_first_run_missing_checksum_stores_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Fresh table: no metadata checksum row yet (read returns None), so the
        # run takes the diff path and persists the checksum. This is also the
        # self-correcting transition off the old SSM checksum (ADR-0034).
        client = self._client()
        monkeypatch.setattr(dynamo, "read_catalog_checksum", lambda: None)  # no row yet
        monkeypatch.setattr(dynamo, "scan_catalog_fingerprints", dict)  # empty table
        monkeypatch.setattr(
            dynamo, "bulk_upsert_catalog_items", lambda items, **k: (len(list(items)), 1)
        )
        writes = self._capture_writes(monkeypatch)

        stats = catalog.sync_catalog(client, ["en", "tw"])  # type: ignore[arg-type]

        assert stats.unchanged is False
        assert len(writes) == 1  # checksum created on first run

    def test_partial_fetch_ignores_checksum(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # tw failed -> incomplete fetch: never consult or store the checksum.
        client = _StubClient({"en": [CatalogEntry(item_id=1, name="A", grade=4)], "tw": []})
        monkeypatch.setattr(dynamo, "scan_catalog_fingerprints", dict)
        monkeypatch.setattr(
            dynamo, "bulk_upsert_catalog_items", lambda items, **k: (len(list(items)), 1)
        )
        reads = {"count": 0}

        def spy_read() -> str | None:
            reads["count"] += 1
            return "anything"

        monkeypatch.setattr(dynamo, "read_catalog_checksum", spy_read)
        writes = self._capture_writes(monkeypatch)

        stats = catalog.sync_catalog(client, ["en", "tw"])  # type: ignore[arg-type]

        assert stats.unchanged is False
        assert reads["count"] == 0  # checksum not consulted on a partial fetch
        assert writes == []  # and not stored

    def test_use_checksum_false_bypasses_fastpath(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # use_checksum=False (e.g. a forced full resync) skips both the checksum
        # read and its persistence even on a complete fetch.
        client = self._client()
        reads = {"count": 0}

        def spy_read() -> str | None:
            reads["count"] += 1
            return self._current_checksum()

        monkeypatch.setattr(dynamo, "read_catalog_checksum", spy_read)
        monkeypatch.setattr(dynamo, "scan_catalog_fingerprints", dict)
        monkeypatch.setattr(
            dynamo, "bulk_upsert_catalog_items", lambda items, **k: (len(list(items)), 1)
        )
        writes = self._capture_writes(monkeypatch)

        stats = catalog.sync_catalog(client, ["en", "tw"], use_checksum=False)  # type: ignore[arg-type]

        assert reads["count"] == 0  # fast-path not consulted
        assert writes == []  # checksum not persisted
        assert stats.unchanged is False
