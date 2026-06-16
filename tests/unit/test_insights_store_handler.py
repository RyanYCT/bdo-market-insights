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


def _make_event_with_narrative(
    digest: MarketDigest, narrative: Narrative | None, model_id: str = "us.amazon.nova-lite-v1:0"
) -> dict[str, Any]:
    """Build event with optional LLM narrative (as from Summarize step)."""
    event = _make_event(digest)
    event["narrative"] = narrative.model_dump(mode="json") if narrative is not None else None
    event["model_id"] = model_id
    return event


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
    assert result["headline"] == "Market summary for tw (daily) - 2026-06-13"


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


def test_emits_summaries_generated_metric_on_success(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SummariesGenerated metric is emitted on successful upsert."""
    monkeypatch.setattr(mod, "render_narrative", lambda d: _make_narrative())
    monkeypatch.setattr(mod.SummaryRepo, "upsert", staticmethod(lambda conn, **kw: None))

    metric_calls: list[dict[str, Any]] = []

    def capture_metric(**kwargs: Any) -> None:
        metric_calls.append(kwargs)

    monkeypatch.setattr(mod.metrics, "add_metric", capture_metric)

    mod.handler(_make_event(_make_digest()), lambda_context)

    assert any(m["name"] == "SummariesGenerated" for m in metric_calls)
    assert not any(m["name"] == "InsightFailures" for m in metric_calls)


def test_emits_insight_failures_metric_on_exception(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """InsightFailures metric is emitted when upsert raises."""
    monkeypatch.setattr(mod, "render_narrative", lambda d: _make_narrative())

    def boom(conn: Any, **kwargs: Any) -> None:
        raise RuntimeError("db error")

    monkeypatch.setattr(mod.SummaryRepo, "upsert", staticmethod(boom))

    metric_calls: list[dict[str, Any]] = []

    def capture_metric(**kwargs: Any) -> None:
        metric_calls.append(kwargs)

    monkeypatch.setattr(mod.metrics, "add_metric", capture_metric)

    with pytest.raises(RuntimeError, match="db error"):
        mod.handler(_make_event(_make_digest()), lambda_context)

    assert any(m["name"] == "InsightFailures" for m in metric_calls)
    assert not any(m["name"] == "SummariesGenerated" for m in metric_calls)


def test_uses_llm_narrative_when_present(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Event with a valid narrative dict uses it directly and passes event model_id to upsert."""
    digest = _make_digest()
    llm_narrative = Narrative(
        headline="LLM generated headline",
        categories=[NarrativeCategory(category="accessory", bullets=["Ogre Ring surged +8%"])],
        overall="Strong bullish momentum across accessories.",
    )
    upsert_calls: list[dict[str, Any]] = []

    def fake_upsert(conn: Any, **kwargs: Any) -> None:
        upsert_calls.append(kwargs)

    monkeypatch.setattr(mod.SummaryRepo, "upsert", staticmethod(fake_upsert))

    event = _make_event_with_narrative(digest, llm_narrative, "us.amazon.nova-lite-v1:0")
    result = mod.handler(event, lambda_context)

    assert len(upsert_calls) == 1
    assert upsert_calls[0]["model_id"] == "us.amazon.nova-lite-v1:0"
    assert upsert_calls[0]["narrative"].headline == "LLM generated headline"
    assert result["model_id"] == "us.amazon.nova-lite-v1:0"
    assert result["headline"] == "LLM generated headline"
    assert result["status"] == "stored"


def test_falls_back_to_deterministic_when_narrative_null(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Event with narrative=null falls back to render_narrative with deterministic-v1."""
    digest = _make_digest()
    deterministic_narrative = _make_narrative()
    upsert_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(mod, "render_narrative", lambda d: deterministic_narrative)

    def fake_upsert(conn: Any, **kwargs: Any) -> None:
        upsert_calls.append(kwargs)

    monkeypatch.setattr(mod.SummaryRepo, "upsert", staticmethod(fake_upsert))

    event = _make_event_with_narrative(digest, None, "deterministic-v1")
    result = mod.handler(event, lambda_context)

    assert len(upsert_calls) == 1
    assert upsert_calls[0]["model_id"] == "deterministic-v1"
    assert upsert_calls[0]["narrative"] == deterministic_narrative
    assert result["model_id"] == "deterministic-v1"
    assert result["status"] == "stored"
