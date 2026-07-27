"""Unit tests for bdo_common.tracking (offline track-selection logic)."""

from __future__ import annotations

from bdo_common import tracking
from bdo_common.models import MarketListItem


def _mli(item_id: int, main: int, sub: int, name: str = "x") -> MarketListItem:
    return MarketListItem(item_id=item_id, name=name, main_category=main, sub_category=sub)


class TestMainCategoryCodes:
    def test_one_then_multiples_of_five(self) -> None:
        assert tracking.main_category_codes(20) == [1, 5, 10, 15, 20]

    def test_default_max(self) -> None:
        assert tracking.main_category_codes()[0] == 1
        assert tracking.main_category_codes()[-1] == 85


class TestEnumerateTaxonomy:
    def test_stops_each_main_on_first_empty_and_dedupes(self) -> None:
        data = {
            (1, 1): [_mli(2, 1, 1), _mli(1, 1, 1)],
            (1, 2): [_mli(3, 1, 2), _mli(2, 1, 1)],  # id 2 repeats -> deduped
            # (1, 3) empty -> stop main 1
            (5, 1): [_mli(4, 5, 1)],
            # (5, 2) empty -> stop main 5
        }

        def fetch(main: int, sub: int) -> list[MarketListItem]:
            return data.get((main, sub), [])

        result = tracking.enumerate_taxonomy(fetch, max_main=5, max_sub=10)
        # Flat, de-duplicated by id, sorted by id.
        assert [r.item_id for r in result] == [1, 2, 3, 4]

    def test_empty_first_sub_skips_whole_main(self) -> None:
        def fetch(main: int, sub: int) -> list[MarketListItem]:
            return [_mli(9, 5, 1)] if (main, sub) == (5, 1) else []

        result = tracking.enumerate_taxonomy(fetch, max_main=5, max_sub=5)
        assert [r.item_id for r in result] == [9]


class TestParseCatalog:
    def test_maps_snapshot_rows(self) -> None:
        rows = [{"id": 12094, "name": "Deboreka Ring", "main": 20, "sub": 1}]
        catalog = tracking.parse_catalog(rows)
        assert catalog[0].item_id == 12094
        assert catalog[0].main_category == 20
        assert catalog[0].sub_category == 1


class TestSelectIds:
    catalog = [_mli(1, 20, 1), _mli(2, 20, 2), _mli(3, 55, 6), _mli(4, 20, 1)]

    def test_by_main(self) -> None:
        assert tracking.select_ids(self.catalog, main=20) == [1, 2, 4]

    def test_by_main_and_sub(self) -> None:
        assert tracking.select_ids(self.catalog, main=20, sub=1) == [1, 4]

    def test_by_ids_drops_stale(self) -> None:
        assert tracking.select_ids(self.catalog, ids=[3, 2, 999]) == [2, 3]

    def test_select_all(self) -> None:
        assert tracking.select_ids(self.catalog, select_all=True) == [1, 2, 3, 4]

    def test_no_spec_is_empty(self) -> None:
        assert tracking.select_ids(self.catalog) == []


class TestDefaultCronProfile:
    def test_accessory_is_standard(self) -> None:
        assert tracking.default_cron_profile("accessory") == "standard"

    def test_non_accessory_is_none(self) -> None:
        assert tracking.default_cron_profile("buff") == "none"

    def test_unknown_is_none(self) -> None:
        assert tracking.default_cron_profile(None) == "none"


class TestBuildTrackedUpdates:
    index = tracking.catalog_index([_mli(1, 20, 1), _mli(5, 55, 6), _mli(9, 99, 9)])
    category_map = {"20:1": "accessory", "55:6": "buff"}

    def test_accessory_defaults_to_standard(self) -> None:
        updates, classified = tracking.build_tracked_updates(
            1, index=self.index, category_map=self.category_map
        )
        assert classified is True
        assert updates == {
            "tracked": "true",
            "main_category": "20",
            "sub_category": "1",
            "category": "accessory",
            "cron_profile": "standard",
        }

    def test_non_accessory_defaults_to_none(self) -> None:
        updates, classified = tracking.build_tracked_updates(
            5, index=self.index, category_map=self.category_map
        )
        assert classified is True
        assert updates["category"] == "buff"
        assert updates["cron_profile"] == "none"

    def test_series_override_wins_over_category_default(self) -> None:
        updates, _ = tracking.build_tracked_updates(
            1, series_profile="deboreka", index=self.index, category_map=self.category_map
        )
        assert updates["cron_profile"] == "deboreka"

    def test_missing_from_snapshot_is_unclassified_and_none(self) -> None:
        updates, classified = tracking.build_tracked_updates(
            404, index=self.index, category_map=self.category_map
        )
        assert classified is False
        assert updates == {"tracked": "true", "cron_profile": "none"}

    def test_known_codes_without_label_is_unclassified(self) -> None:
        updates, classified = tracking.build_tracked_updates(
            9, index=self.index, category_map=self.category_map
        )
        assert classified is False
        assert updates["main_category"] == "99"
        assert "category" not in updates
        assert updates["cron_profile"] == "none"

    def test_model_id_passthrough(self) -> None:
        updates, _ = tracking.build_tracked_updates(
            1, index=self.index, category_map=self.category_map, model_id="buff_v1"
        )
        assert updates["model_id"] == "buff_v1"


class TestCronOverrides:
    def test_only_sets_with_cron_profile(self) -> None:
        sets = {
            "_comment": "ignored",
            "deboreka": {"cron_profile": "deboreka", "ids": [12094, 11653]},
            "buffs": {"ids": [17081]},
        }
        assert tracking.cron_overrides(sets) == {12094: "deboreka", 11653: "deboreka"}


class TestReconcile:
    def test_ids_to_untrack(self) -> None:
        assert tracking.ids_to_untrack({1, 2, 3}, {2, 3, 4}) == [1]

    def test_nothing_to_untrack(self) -> None:
        assert tracking.ids_to_untrack({1, 2}, {1, 2, 3}) == []


class TestNeedsConfirmation:
    def test_small_selection_ok(self) -> None:
        assert tracking.needs_confirmation(50) is False

    def test_boundary_not_guarded(self) -> None:
        assert tracking.needs_confirmation(tracking.MAX_UNGUARDED_SELECTION) is False

    def test_over_threshold_guarded(self) -> None:
        assert tracking.needs_confirmation(tracking.MAX_UNGUARDED_SELECTION + 1) is True

    def test_select_all_always_guarded(self) -> None:
        assert tracking.needs_confirmation(1, select_all=True) is True
