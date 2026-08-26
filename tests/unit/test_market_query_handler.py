"""Unit tests for the marketQuery API handler (FR-13..15).

Drives the Powertools resolver with synthetic API Gateway REST proxy events;
the DB connection and repositories are mocked, while the real pricing/analytics
domain functions run.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest

from bdo_common.models import DailyRow, SnapshotRow


def _event(path: str, *, query: dict[str, str] | None = None) -> dict[str, Any]:
    """Build a minimal API Gateway REST proxy GET event."""
    return {
        "resource": path,
        "path": path,
        "httpMethod": "GET",
        "headers": {"Accept": "application/json"},
        "queryStringParameters": query,
        "pathParameters": None,
        "requestContext": {"requestId": "req-1", "stage": "test", "httpMethod": "GET"},
        "body": None,
        "isBase64Encoded": False,
    }


def _snap(sid: int, price: int) -> SnapshotRow:
    return SnapshotRow(
        region="tw",
        snapshot_at=datetime(2026, 3, 15, 5, tzinfo=UTC),
        item_id=12094,
        sid=sid,
        base_price=price,
        current_stock=10,
        total_trades=100,
        last_sold_price=price - 1,
        last_sold_at=datetime(2026, 3, 15, 4, tzinfo=UTC),
    )


def _daily(i: int) -> DailyRow:
    return DailyRow(
        region="tw",
        trade_date=date(2026, 3, 1 + i),
        item_id=12094,
        sid=0,
        open_price=100 + i,
        high_price=110 + i,
        low_price=90 + i,
        close_price=100 + i,
        avg_price=100 + i,
        total_trades_delta=10 + i,
        avg_stock=5,
        snapshot_count=24,
    )


@pytest.fixture
def mod(load_handler: Callable[[str], ModuleType], monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = load_handler("market_query")
    # The handler opens a real connection via db.get_connection; stub it out.
    monkeypatch.setattr(module.db, "get_connection", lambda: MagicMock())
    return module


def test_snapshots_caps_limit_and_passes_filters(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake(conn: Any, **kwargs: Any) -> list[SnapshotRow]:
        captured.update(kwargs)
        return [_snap(0, 453_000_000)]

    monkeypatch.setattr(mod.SnapshotRepo, "get_snapshots", fake)
    resp = mod.handler(
        _event("/v1/market/items/12094/snapshots", query={"limit": "5000", "sid": "0"}),
        lambda_context,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["count"] == 1
    assert body["item_id"] == 12094
    assert captured["region"] == "tw"  # default
    assert captured["item_id"] == 12094
    assert captured["sid"] == 0
    assert captured["limit"] == 1848  # capped at MAX_SNAPSHOT_LIMIT


def test_snapshots_default_window_and_cap(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare call defaults to a trailing 168h time window and the full row cap."""
    captured: dict[str, Any] = {}

    def fake(conn: Any, **kwargs: Any) -> list[SnapshotRow]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(mod.SnapshotRepo, "get_snapshots", fake)
    resp = mod.handler(_event("/v1/market/items/12094/snapshots"), lambda_context)

    assert resp["statusCode"] == 200
    assert captured["limit"] == mod.MAX_SNAPSHOT_LIMIT == 1848
    assert captured["to_dt"] is None  # upper bound left unbounded
    # from defaulted to ~168h before now (a time window, independent of sid count)
    delta = datetime.now(tz=UTC) - captured["from_dt"]
    assert abs(delta - timedelta(hours=168)) < timedelta(minutes=1)


def test_snapshots_reports_coverage_with_gap(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """coverage counts distinct hourly buckets and flags the missing hour."""

    def _at(hour: int) -> SnapshotRow:
        return SnapshotRow(
            region="tw",
            snapshot_at=datetime(2026, 3, 15, hour, tzinfo=UTC),
            item_id=12094,
            sid=0,
            base_price=100,
            current_stock=1,
            total_trades=1,
            last_sold_price=99,
            last_sold_at=datetime(2026, 3, 15, hour, tzinfo=UTC),
        )

    # newest-first, hours 5,4,2 present -> hour 3 missing over the [2,5] span
    monkeypatch.setattr(
        mod.SnapshotRepo, "get_snapshots", lambda conn, **kw: [_at(5), _at(4), _at(2)]
    )
    resp = mod.handler(
        _event(
            "/v1/market/items/12094/snapshots",
            query={"sid": "0", "from": "2026-03-15T02:00:00Z", "to": "2026-03-15T05:00:00Z"},
        ),
        lambda_context,
    )

    body = json.loads(resp["body"])
    cov = body["coverage"]
    assert cov["expected_hours"] == 4
    assert cov["present_hours"] == 3
    assert cov["missing_hours"] == 1
    assert cov["window_start"].startswith("2026-03-15T02:00:00")
    assert cov["window_end"].startswith("2026-03-15T05:00:00")
    assert cov["truncated"] is False  # 3 rows < cap


def test_snapshots_coverage_null_when_empty_and_unbounded(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No rows and no from/to -> no window to describe, coverage is null."""
    monkeypatch.setattr(mod.SnapshotRepo, "get_snapshots", lambda conn, **kw: [])
    resp = mod.handler(_event("/v1/market/items/12094/snapshots"), lambda_context)

    body = json.loads(resp["body"])
    assert body["count"] == 0
    assert body["coverage"] is None


def test_snapshots_rejects_bad_integer(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mod.SnapshotRepo, "get_snapshots", lambda conn, **kw: [])
    resp = mod.handler(
        _event("/v1/market/items/12094/snapshots", query={"sid": "abc"}), lambda_context
    )
    assert resp["statusCode"] == 400


def test_daily_returns_rows(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mod.DailyRepo, "get_daily", lambda conn, **kw: [_daily(0), _daily(1)])
    resp = mod.handler(
        _event("/v1/market/items/12094/daily", query={"region": "tw"}), lambda_context
    )
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["count"] == 2


def test_daily_default_window_and_cap(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare /daily call defaults to a trailing 90-day window and the full cap."""
    captured: dict[str, Any] = {}

    def fake(conn: Any, **kwargs: Any) -> list[DailyRow]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(mod.DailyRepo, "get_daily", fake)
    resp = mod.handler(_event("/v1/market/items/12094/daily"), lambda_context)

    assert resp["statusCode"] == 200
    assert captured["limit"] == mod.MAX_DAILY_LIMIT == 990
    assert captured["to_date"] is None
    delta = datetime.now(tz=UTC).date() - captured["from_date"]
    assert abs(delta.days - 90) <= 1


def test_daily_reports_coverage_with_gap(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """coverage counts distinct trade_date days and flags the missing day."""

    def _at(day: int) -> DailyRow:
        return DailyRow(
            region="tw",
            trade_date=date(2026, 3, day),
            item_id=12094,
            sid=0,
            open_price=1,
            high_price=1,
            low_price=1,
            close_price=1,
            avg_price=1,
            total_trades_delta=1,
            avg_stock=1,
            snapshot_count=24,
        )

    # days 1,2,4 present -> day 3 missing over the [1,4] window
    monkeypatch.setattr(mod.DailyRepo, "get_daily", lambda conn, **kw: [_at(4), _at(2), _at(1)])
    resp = mod.handler(
        _event(
            "/v1/market/items/12094/daily",
            query={"sid": "0", "from": "2026-03-01", "to": "2026-03-04"},
        ),
        lambda_context,
    )

    body = json.loads(resp["body"])
    cov = body["coverage"]
    assert cov["expected_days"] == 4
    assert cov["present_days"] == 3
    assert cov["missing_days"] == 1
    assert cov["window_start"] == "2026-03-01"
    assert cov["window_end"] == "2026-03-04"
    assert cov["truncated"] is False


def test_daily_coverage_null_when_empty_and_unbounded(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No rows and no explicit window -> nothing to describe, coverage is null."""
    monkeypatch.setattr(mod.DailyRepo, "get_daily", lambda conn, **kw: [])
    resp = mod.handler(_event("/v1/market/items/12094/daily"), lambda_context)

    body = json.loads(resp["body"])
    assert body["count"] == 0
    assert body["coverage"] is None


def test_analysis_combines_pricing_and_analytics(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ladder = [
        _snap(0, 453_000_000),
        _snap(1, 1_170_000_000),
        _snap(2, 3_540_000_000),
        _snap(3, 9_450_000_000),
        _snap(4, 28_700_000_000),
        _snap(5, 186_000_000_000),
    ]
    monkeypatch.setattr(mod.SnapshotRepo, "get_snapshots", lambda conn, **kw: ladder)
    monkeypatch.setattr(
        mod.DailyRepo, "get_daily_window", lambda conn, **kw: [_daily(i) for i in range(7)]
    )

    resp = mod.handler(_event("/v1/market/items/12094/analysis"), lambda_context)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    # Enhancement ladder present (real accessory_v1 model over the price ladder).
    assert body["enhancement"] is not None
    assert {(t["sid_from"], t["sid_to"]) for t in body["enhancement"]["transitions"]} >= {(0, 1)}
    # Analytics computed (7 daily points >= MIN_POINTS).
    assert body["analytics"]["insufficient_data"] is False
    assert body["sid"] == 0  # default sid


def test_analysis_insufficient_daily_data(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        mod.SnapshotRepo, "get_snapshots", lambda conn, **kw: [_snap(0, 453_000_000)]
    )
    monkeypatch.setattr(
        mod.DailyRepo, "get_daily_window", lambda conn, **kw: [_daily(0), _daily(1)]
    )
    resp = mod.handler(_event("/v1/market/items/12094/analysis"), lambda_context)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["analytics"]["insufficient_data"] is True


def test_snapshots_rejects_invalid_region(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mod.SnapshotRepo, "get_snapshots", lambda conn, **kw: [])
    resp = mod.handler(
        _event("/v1/market/items/12094/snapshots", query={"region": "atlantis"}),
        lambda_context,
    )
    assert resp["statusCode"] == 400


def test_snapshots_parses_datetime_filters(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake(conn: Any, **kwargs: Any) -> list[SnapshotRow]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(mod.SnapshotRepo, "get_snapshots", fake)
    resp = mod.handler(
        _event(
            "/v1/market/items/12094/snapshots",
            query={"from": "2026-03-01T00:00:00Z", "to": "2026-03-15T00:00:00Z"},
        ),
        lambda_context,
    )
    assert resp["statusCode"] == 200
    assert captured["from_dt"] == datetime(2026, 3, 1, tzinfo=UTC)
    assert captured["to_dt"] == datetime(2026, 3, 15, tzinfo=UTC)


def test_daily_rejects_bad_date(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mod.DailyRepo, "get_daily", lambda conn, **kw: [])
    resp = mod.handler(
        _event("/v1/market/items/12094/daily", query={"from": "not-a-date"}), lambda_context
    )
    assert resp["statusCode"] == 400


def test_analysis_rejects_window_days_out_of_range(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    resp = mod.handler(
        _event("/v1/market/items/12094/analysis", query={"window_days": "999"}), lambda_context
    )
    assert resp["statusCode"] == 400


# ──────────────────────────────────────────────────────────────────────────────
# /v1/insights route tests
# ──────────────────────────────────────────────────────────────────────────────


def _market_summary() -> Any:
    """Build a fake MarketSummary for testing."""
    from bdo_common.insights.models import (
        MarketDigest,
        MarketSummary,
        Narrative,
        NarrativeCategory,
    )

    return MarketSummary(
        region="tw",
        period="daily",
        summary_date=date(2026, 6, 13),
        lang="en",
        model_id="deterministic-v1",
        digest=MarketDigest(
            region="tw",
            period="daily",
            summary_date=date(2026, 6, 13),
            top_n=5,
            entries=[],
            generated_at=datetime(2026, 6, 14, 1, 0, 0, tzinfo=UTC),
        ),
        narrative=Narrative(
            headline="Market summary for tw (daily) - 2026-06-13",
            categories=[NarrativeCategory(category="accessory", bullets=["item +5%"])],
            overall="5 items tracked across 1 categories: 3 gainers, 2 losers.",
        ),
        created_at=datetime(2026, 6, 14, 1, 5, 0, tzinfo=UTC),
    )


def test_insights_returns_summary(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mod.SummaryRepo, "get", staticmethod(lambda conn, **kw: _market_summary()))
    resp = mod.handler(_event("/v1/insights", query={"region": "tw"}), lambda_context)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["region"] == "tw"
    assert body["period"] == "daily"
    assert body["summary_date"] == "2026-06-13"
    assert body["model_id"] == "deterministic-v1"
    assert "headline" in body["narrative"]
    assert "entries" in body["digest"]


def test_insights_returns_404_when_no_summary(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mod.SummaryRepo, "get", staticmethod(lambda conn, **kw: None))
    resp = mod.handler(_event("/v1/insights"), lambda_context)
    assert resp["statusCode"] == 404
    body = json.loads(resp["body"])
    assert body["message"] == "No summary found"


def test_insights_rejects_invalid_region(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mod.SummaryRepo, "get", staticmethod(lambda conn, **kw: None))
    resp = mod.handler(_event("/v1/insights", query={"region": "atlantis"}), lambda_context)
    assert resp["statusCode"] == 400


def test_insights_rejects_invalid_period(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mod.SummaryRepo, "get", staticmethod(lambda conn, **kw: None))
    resp = mod.handler(_event("/v1/insights", query={"period": "hourly"}), lambda_context)
    assert resp["statusCode"] == 400
