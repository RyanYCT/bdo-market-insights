"""End-to-end ETL integration tests against a real Postgres + moto DynamoDB.

Drives the actual handlers (retrieveItems -> fetchData -> cleanData -> storeData,
then rollupDaily and purgeOldSnapshots) with only arsha.io HTTP stubbed, and
asserts the rows that land in Postgres. Skipped unless ``TEST_DATABASE_URL`` is
set (see ``conftest.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import ModuleType, SimpleNamespace
from typing import Any

import psycopg
import pytest

from bdo_common import dynamo
from bdo_common.models import Item

pytestmark = pytest.mark.integration


def _arsha_item(item_id: int, sid: int, *, price: int, total_trades: int) -> dict[str, Any]:
    """Build one arsha.io v2 item object for the given (id, sid)."""
    return {
        "id": item_id,
        "sid": sid,
        "name": f"Item {item_id}",
        "minEnhance": 0,
        "maxEnhance": 5,
        "basePrice": price,
        "currentStock": 100,
        "totalTrades": total_trades,
        "priceMin": 1,
        "priceMax": 1_000_000_000,
        "lastSoldPrice": price - 5,
        "lastSoldTime": 1_717_027_200,
    }


def _make_fetch_raw(
    *, price: int, total_trades: int, sids: tuple[int, ...]
) -> Callable[[Any, list[int]], list[Any]]:
    """Build a fake ``ArshaClient.fetch_raw`` returning canned payloads."""

    def _fetch_raw(self: Any, item_ids: list[int]) -> list[Any]:
        return [
            [_arsha_item(item_id, sid, price=price, total_trades=total_trades) for sid in sids]
            for item_id in item_ids
        ]

    return _fetch_raw


def _scalar(
    conn: psycopg.Connection[tuple[Any, ...]], sql: str, params: tuple[Any, ...] = ()
) -> Any:
    """Run ``sql`` and return the first column of the single result row."""
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    return row[0]


def _seed(*items: Item) -> None:
    """Write items into the moto-mocked DynamoDB table."""
    for item in items:
        dynamo.put_item(item)


@pytest.fixture
def etl(load_handler: Callable[[str], ModuleType]) -> Any:
    """Load all six ETL handler modules once for the test."""
    return SimpleNamespace(
        retrieve=load_handler("retrieve_items"),
        fetch=load_handler("fetch_data"),
        clean=load_handler("clean_data"),
        store=load_handler("store_data"),
        rollup=load_handler("rollup_daily"),
        purge=load_handler("purge_old_snapshots"),
    )


@pytest.fixture
def run_cycle(
    etl: Any,
    lambda_context: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., dict[str, Any]]:
    """Return a callable that runs one hourly ETL cycle end-to-end.

    arsha.io is stubbed per cycle so the batch's items resolve to deterministic
    prices/trade counts; the rest of the read->store path runs for real.
    """

    def _run(
        *,
        region: str,
        execution_start_time: str,
        price: int,
        total_trades: int = 1000,
        sids: tuple[int, ...] = (0, 1),
    ) -> dict[str, Any]:
        monkeypatch.setattr(
            etl.fetch.ArshaClient,
            "fetch_raw",
            _make_fetch_raw(price=price, total_trades=total_trades, sids=sids),
        )
        retrieved: dict[str, Any] = etl.retrieve.handler(
            {"region": region, "execution_start_time": execution_start_time},
            lambda_context,
        )
        for batch in retrieved["batches"]:
            fetched = etl.fetch.handler(batch, lambda_context)
            cleaned = etl.clean.handler(fetched, lambda_context)
            etl.store.handler(cleaned, lambda_context)
        return retrieved

    return _run


def test_full_cycle_persists_items_sids_and_snapshots(
    dynamo_table: None,
    db_conn: psycopg.Connection[tuple[Any, ...]],
    run_cycle: Callable[..., dict[str, Any]],
) -> None:
    _seed(
        Item(id=11608, name="Deboreka Necklace", category="necklace", tracked=True),
        Item(id=11629, name="Deboreka Ring", category="ring", tracked=True),
        Item(id=99999, name="Untracked Junk", category="ring", tracked=False),
    )

    result = run_cycle(region="tw", execution_start_time="2026-03-15T05:30:00Z", price=1000)
    assert result["snapshot_at"] == "2026-03-15T05:00:00+00:00"

    # Only the two tracked items were scanned, fetched, and stored.
    item_ids = [r[0] for r in db_conn.execute("SELECT id FROM item ORDER BY id").fetchall()]
    assert item_ids == [11608, 11629]

    # Two sids (0, 1) per item -> four item_sid rows.
    assert _scalar(db_conn, "SELECT count(*) FROM item_sid") == 4

    rows = db_conn.execute(
        "SELECT item_id, sid, base_price, snapshot_at FROM market_snapshot ORDER BY item_id, sid"
    ).fetchall()
    assert {(r[0], r[1]) for r in rows} == {(11608, 0), (11608, 1), (11629, 0), (11629, 1)}
    assert all(r[2] == 1000 for r in rows)
    assert all(r[3] == datetime(2026, 3, 15, 5, 0, tzinfo=UTC) for r in rows)


def test_same_hour_rerun_is_idempotent(
    dynamo_table: None,
    db_conn: psycopg.Connection[tuple[Any, ...]],
    run_cycle: Callable[..., dict[str, Any]],
) -> None:
    _seed(Item(id=11608, name="Deboreka Necklace", category="necklace", tracked=True))

    run_cycle(region="tw", execution_start_time="2026-03-15T05:30:00Z", price=1000)
    first = _scalar(db_conn, "SELECT count(*) FROM market_snapshot")

    # A second run in the same hour truncates to the same snapshot_at, so the
    # ON CONFLICT DO NOTHING insert is a no-op (NFR-4) -- even with a new price.
    run_cycle(region="tw", execution_start_time="2026-03-15T05:55:00Z", price=2000)
    second = _scalar(db_conn, "SELECT count(*) FROM market_snapshot")

    assert first == second == 2
    retained = _scalar(db_conn, "SELECT base_price FROM market_snapshot WHERE sid = 0")
    assert retained == 1000


def test_rollup_aggregates_daily_ohlc(
    dynamo_table: None,
    db_conn: psycopg.Connection[tuple[Any, ...]],
    run_cycle: Callable[..., dict[str, Any]],
    etl: Any,
    lambda_context: Any,
) -> None:
    _seed(Item(id=11608, name="Deboreka Necklace", category="necklace", tracked=True))

    # Three hourly snapshots on the same UTC day for sid 0.
    run_cycle(
        region="tw",
        execution_start_time="2026-03-15T02:00:00Z",
        price=1000,
        total_trades=1000,
        sids=(0,),
    )
    run_cycle(
        region="tw",
        execution_start_time="2026-03-15T05:00:00Z",
        price=1500,
        total_trades=1200,
        sids=(0,),
    )
    run_cycle(
        region="tw",
        execution_start_time="2026-03-15T09:00:00Z",
        price=800,
        total_trades=1500,
        sids=(0,),
    )

    # snapshot_at is midnight of the *next* day -> rolls up 2026-03-15.
    result = etl.rollup.handler(
        {"region": "tw", "snapshot_at": "2026-03-16T00:00:00+00:00"}, lambda_context
    )
    assert result == {"region": "tw", "trade_date": "2026-03-15", "daily_rows": 1}

    row = db_conn.execute(
        "SELECT open_price, high_price, low_price, close_price, avg_price, "
        "total_trades_delta, snapshot_count FROM market_daily "
        "WHERE region = 'tw' AND item_id = 11608 AND sid = 0 AND trade_date = '2026-03-15'"
    ).fetchone()
    assert row is not None
    # open=first(02:00), close=last(09:00), avg=(1000+1500+800)/3, delta=1500-1000.
    assert row == (1000, 1500, 800, 800, 1100, 500, 3)


def test_purge_removes_snapshots_older_than_retention(
    dynamo_table: None,
    db_conn: psycopg.Connection[tuple[Any, ...]],
    run_cycle: Callable[..., dict[str, Any]],
    etl: Any,
    lambda_context: Any,
) -> None:
    _seed(Item(id=11608, name="Deboreka Necklace", category="necklace", tracked=True))

    # A recent cycle creates item, item_sid, and a fresh snapshot (~2 days old).
    recent_hour = (datetime.now(tz=UTC) - timedelta(days=2)).replace(
        minute=0, second=0, microsecond=0
    )
    run_cycle(region="tw", execution_start_time=recent_hour.isoformat(), price=1000, sids=(0,))

    # Inject an old snapshot (>90d) for the same (region, item, sid).
    old_at = datetime.now(tz=UTC) - timedelta(days=120)
    db_conn.execute(
        "INSERT INTO market_snapshot (region, snapshot_at, item_id, sid, base_price, "
        "current_stock, total_trades, last_sold_price, last_sold_at) "
        "VALUES ('tw', %s, 11608, 0, 500, 10, 10, 495, %s)",
        (old_at, old_at),
    )
    assert _scalar(db_conn, "SELECT count(*) FROM market_snapshot") == 2

    result = etl.purge.handler({}, lambda_context)
    assert result["deleted"] == 1

    assert _scalar(db_conn, "SELECT count(*) FROM market_snapshot") == 1
    assert _scalar(db_conn, "SELECT base_price FROM market_snapshot") == 1000
