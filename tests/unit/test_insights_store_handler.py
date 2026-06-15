"""Unit tests for the insightsStore Lambda handler."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest

from bdo_common.insights.models import MarketDigest, Narrative, NarrativeCategory


def _make_digest() -> MarketDigest:
    return MarketDigest(
        region="tw",
        period="daily",
        summary_date=date(2026, 6, 13),
        top_n=5,
        entries=[],
        generated_at=datetime(2026, 6, 14, 1, 0, 0, tzinfo=UTC),
    )


def _make_narrative() -> Narrative:
    return Narrative(
        headline="Market summary for tw (daily) - 2026-06-13",
        categories=[NarrativeCategory(category="accessory", bullets=["item +5%"])],
        overall="5 items tracked across 1 categories: 3 gainers, 2 losers.",
    )


def _make_event(digest: MarketDigest) -> dict[str, Any]:
    return {
        "region": "tw",
        "period": "daily",
        "target_date": "2026-06-13",
        "digest": digest.model_dump(mode="json"),
    }


@pytest.fixture
def mod(load_handler: Callable[[str], ModuleType], monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = load_handler("insights_store")
    monkeypatch.setattr(module.db, "get_connection", lambda: MagicMock())
    return module


def test_renders_narrative_and_upserts(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = _make_digest()
    narrative = _make_narrative()
    upsert_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(mod, "render_narrative", lambda d: narrative)

    def fake_upsert(conn: Any, **kwargs: Any) -> None:
        upsert_calls.append(kwargs)

    monkeypatch.setattr(mod.SummaryRepo, "upsert", staticmethod(fake_upsert))

    result = mod.handler(_make_event(digest), lambda_context)

    assert len(upsert_calls) == 1
    assert upsert_calls[0]["region"] == "tw"
    assert upsert_calls[0]["period"] == "daily"
    assert upsert_calls[0]["summary_date"] == date(2026, 6, 13)
    assert upsert_calls[0]["lang"] == "en"
    assert upsert_calls[0]["model_id"] == "deterministic-v1"
    assert result["status"] == "stored"


def test_commits_on_success(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_conn = MagicMock()
    monkeypatch.setattr(mod.db, "get_connection", lambda: mock_conn)
    monkeypatch.setattr(mod, "render_narrative", lambda d: _make_narrative())
    monkeypatch.setattr(mod.SummaryRepo, "upsert", staticmethod(lambda conn, **kw: None))

    mod.handler(_make_event(_make_digest()), lambda_context)

    mock_conn.commit.assert_called_once()
    mock_conn.rollback.assert_not_called()


def test_rolls_back_on_exception(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_conn = MagicMock()
    monkeypatch.setattr(mod.db, "get_connection", lambda: mock_conn)
    monkeypatch.setattr(mod, "render_narrative", lambda d: _make_narrative())

    def boom(conn: Any, **kwargs: Any) -> None:
        raise RuntimeError("db error")

    monkeypatch.setattr(mod.SummaryRepo, "upsert", staticmethod(boom))

    with pytest.raises(RuntimeError, match="db error"):
        mod.handler(_make_event(_make_digest()), lambda_context)

    mock_conn.rollback.assert_called_once()
    mock_conn.commit.assert_not_called()
