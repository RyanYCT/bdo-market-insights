"""Integration test for RegionRepo.region_availability against real Postgres.

Exercises the actual GROUP BY / COUNT(DISTINCT) SQL (not just the Python merge)
so a broken aggregate can't pass. Skips unless TEST_DATABASE_URL is set.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import psycopg
import pytest

from bdo_common.repositories import RegionRepo

pytestmark = pytest.mark.integration


def _seed_parents(
    conn: psycopg.Connection[tuple[Any, ...]],
    *,
    region: str,
    item_id: int,
) -> None:
    """Insert the item + item_sid parents that snapshot/daily FK-reference."""
    conn.execute(
        "INSERT INTO item (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
        (item_id, f"Item {item_id}"),
    )
    conn.execute(
        "INSERT INTO item_sid (region, item_id, sid, max_enhance, price_min, price_max) "
        "VALUES (%s, %s, 0, 0, 1, 1000) ON CONFLICT DO NOTHING",
        (region, item_id),
    )


def _insert_snapshot(
    conn: psycopg.Connection[tuple[Any, ...]],
    *,
    region: str,
    item_id: int,
    at: datetime,
) -> None:
    _seed_parents(conn, region=region, item_id=item_id)
    conn.execute(
        "INSERT INTO market_snapshot (region, snapshot_at, item_id, sid, base_price, "
        "current_stock, total_trades, last_sold_price, last_sold_at) "
        "VALUES (%s, %s, %s, 0, 500, 10, 10, 495, %s)",
        (region, at, item_id, at),
    )


def test_region_availability_reports_presence(
    db_conn: psycopg.Connection[tuple[Any, ...]],
) -> None:
    ts_tw = datetime(2026, 3, 15, 5, tzinfo=UTC)
    ts_na = datetime(2026, 3, 15, 4, tzinfo=UTC)
    # tw: two distinct items snapshotted; na: one item.
    _insert_snapshot(db_conn, region="tw", item_id=11608, at=ts_tw)
    _insert_snapshot(db_conn, region="tw", item_id=11609, at=ts_tw)
    _insert_snapshot(db_conn, region="na", item_id=12000, at=ts_na)
    # tw has a daily rollup and an insights summary; na has neither.
    db_conn.execute(
        "INSERT INTO market_daily (region, trade_date, item_id, sid, open_price, high_price, "
        "low_price, close_price, avg_price, total_trades_delta, avg_stock, snapshot_count) "
        "VALUES ('tw', %s, 11608, 0, 500, 510, 490, 505, 500, 10, 5, 24)",
        (date(2026, 3, 14),),
    )
    db_conn.execute(
        "INSERT INTO market_summary (region, period, summary_date, lang, model_id, digest, "
        "narrative) VALUES ('tw', 'daily', %s, 'en', 'deterministic-v1', "
        "'{}'::jsonb, '{}'::jsonb)",
        (date(2026, 3, 14),),
    )

    availability = RegionRepo.region_availability(db_conn)

    assert set(availability) == {"tw", "na"}

    tw = availability["tw"]
    assert tw.item_count == 2
    assert tw.latest_snapshot_at == ts_tw
    assert tw.latest_daily_date == date(2026, 3, 14)
    assert tw.has_insights is True

    na = availability["na"]
    assert na.item_count == 1
    assert na.latest_snapshot_at == ts_na
    assert na.latest_daily_date is None
    assert na.has_insights is False


def test_region_availability_empty_when_no_data(
    db_conn: psycopg.Connection[tuple[Any, ...]],
) -> None:
    assert RegionRepo.region_availability(db_conn) == {}
