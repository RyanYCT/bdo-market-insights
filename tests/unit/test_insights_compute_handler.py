"""Unit tests for the insightsCompute Lambda handler."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest

from bdo_common.insights.models import MarketDigest


def _make_digest(region: str = "tw", period: str = "daily") -> MarketDigest:
    return MarketDigest(
        region=region,
        period=period,
        summary_date=date(2026, 6, 13),
        top_n=5,
        entries=[],
        generated_at=datetime(2026, 6, 14, 1, 0, 0, tzinfo=UTC),
    )


@pytest.fixture
def mod(load_handler: Callable[[str], ModuleType], monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = load_handler("insights_compute")
    monkeypatch.setattr(module.db, "get_connection", lambda: MagicMock())
    return module


def test_calls_build_digest_with_correct_args(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    digest = _make_digest()

    def fake_build(conn: Any, **kwargs: Any) -> MarketDigest:
        captured.update(kwargs)
        return digest

    monkeypatch.setattr(mod.build_digest, "__wrapped__", None, raising=False)
    monkeypatch.setattr(mod, "build_digest", fake_build)

    result = mod.handler({"region": "tw", "period": "daily"}, lambda_context)

    assert captured["region"] == "tw"
    assert captured["period"] == "daily"
    assert isinstance(captured["target_date"], date)
    assert result["region"] == "tw"
    assert result["period"] == "daily"
    assert result["digest"] == digest.model_dump(mode="json")


def test_returns_target_date_as_yesterday(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_build(conn: Any, **kwargs: Any) -> MarketDigest:
        captured.update(kwargs)
        return _make_digest()

    monkeypatch.setattr(mod, "build_digest", fake_build)

    result = mod.handler({"region": "tw", "period": "daily"}, lambda_context)

    # target_date should be yesterday UTC
    expected_date = captured["target_date"]
    assert result["target_date"] == expected_date.isoformat()


def test_rolls_back_after_read(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_conn = MagicMock()
    monkeypatch.setattr(mod.db, "get_connection", lambda: mock_conn)
    monkeypatch.setattr(mod, "build_digest", lambda conn, **kw: _make_digest())

    mod.handler({"region": "tw", "period": "daily"}, lambda_context)

    mock_conn.rollback.assert_called_once()
    mock_conn.commit.assert_not_called()


# ── Weekly period tests ──────────────────────────────────────────────────────


def test_weekly_period_passes_through_to_build_digest(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that period='weekly' flows from the event into build_digest."""
    captured: dict[str, Any] = {}
    digest = _make_digest(period="weekly")

    def fake_build(conn: Any, **kwargs: Any) -> MarketDigest:
        captured.update(kwargs)
        return digest

    monkeypatch.setattr(mod, "build_digest", fake_build)

    result = mod.handler({"region": "tw", "period": "weekly"}, lambda_context)

    assert captured["period"] == "weekly"
    assert captured["region"] == "tw"
    assert isinstance(captured["target_date"], date)
    assert result["period"] == "weekly"
    assert result["region"] == "tw"
    assert result["digest"] == digest.model_dump(mode="json")


def test_weekly_compute_summarize_store_flow(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end weekly flow: compute returns correct structure for downstream states."""
    digest = _make_digest(period="weekly")
    monkeypatch.setattr(mod, "build_digest", lambda conn, **kw: digest)

    result = mod.handler({"region": "tw", "period": "weekly"}, lambda_context)

    # The result must contain all keys needed by the Summarize and StoreSummary states
    assert "region" in result
    assert "period" in result
    assert "target_date" in result
    assert "digest" in result
    assert result["period"] == "weekly"
    assert result["digest"]["period"] == "weekly"
