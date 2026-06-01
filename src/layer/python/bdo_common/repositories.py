"""Repository pattern for RDS Postgres. Parameterized SQL, no ORM."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from bdo_common.models import DailyRow, SnapshotRow

if TYPE_CHECKING:
    import psycopg

logger = logging.getLogger(__name__)


class ItemRepo:
    """item table operations."""

    @staticmethod
    def upsert(
        conn: psycopg.Connection[tuple[Any, ...]],
        *,
        item_id: int,
        name: str,
        category: str | None = None,
        main_category: str | None = None,
        sub_category: str | None = None,
    ) -> None:
        """INSERT ... ON CONFLICT (id) DO UPDATE."""
        sql = """
            INSERT INTO item (id, name, category, main_category, sub_category, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                category = EXCLUDED.category,
                main_category = EXCLUDED.main_category,
                sub_category = EXCLUDED.sub_category,
                updated_at = NOW()
        """
        conn.execute(sql, (item_id, name, category, main_category, sub_category))


class ItemSidRepo:
    """item_sid table operations."""

    @staticmethod
    def upsert(
        conn: psycopg.Connection[tuple[Any, ...]],
        *,
        region: str,
        item_id: int,
        sid: int,
        max_enhance: int,
        price_min: int,
        price_max: int,
    ) -> None:
        """INSERT ... ON CONFLICT (region, item_id, sid) DO UPDATE."""
        sql = """
            INSERT INTO item_sid
                (region, item_id, sid, max_enhance, price_min, price_max, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (region, item_id, sid) DO UPDATE SET
                max_enhance = EXCLUDED.max_enhance,
                price_min = EXCLUDED.price_min,
                price_max = EXCLUDED.price_max,
                updated_at = NOW()
        """
        conn.execute(sql, (region, item_id, sid, max_enhance, price_min, price_max))


class SnapshotRepo:
    """market_snapshot table operations."""

    @staticmethod
    def bulk_insert(
        conn: psycopg.Connection[tuple[Any, ...]],
        rows: list[SnapshotRow],
    ) -> int:
        """Bulk INSERT snapshots. Returns count of rows attempted.

        Uses ON CONFLICT DO NOTHING for idempotency on
        (region, item_id, sid, snapshot_at). Duplicate rows are silently
        skipped, so the actual number of rows inserted may be lower than
        the returned count.
        """
        if not rows:
            return 0
        sql = """
            INSERT INTO market_snapshot
                (region, snapshot_at, item_id, sid, base_price, current_stock,
                 total_trades, last_sold_price, last_sold_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (region, item_id, sid, snapshot_at) DO NOTHING
        """
        params = [
            (
                r.region,
                r.snapshot_at,
                r.item_id,
                r.sid,
                r.base_price,
                r.current_stock,
                r.total_trades,
                r.last_sold_price,
                r.last_sold_at,
            )
            for r in rows
        ]
        cur = conn.cursor()
        cur.executemany(sql, params)
        return len(rows)

    @staticmethod
    def get_snapshots(
        conn: psycopg.Connection[tuple[Any, ...]],
        *,
        region: str,
        item_id: int,
        sid: int | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        limit: int = 1000,
    ) -> list[SnapshotRow]:
        """SELECT snapshots with parameterized filters."""
        conditions = ["region = %s", "item_id = %s"]
        params: list[object] = [region, item_id]

        if sid is not None:
            conditions.append("sid = %s")
            params.append(sid)
        if from_dt is not None:
            conditions.append("snapshot_at >= %s")
            params.append(from_dt)
        if to_dt is not None:
            conditions.append("snapshot_at <= %s")
            params.append(to_dt)

        params.append(limit)

        sql = f"""
            SELECT region, snapshot_at, item_id, sid, base_price, current_stock,
                   total_trades, last_sold_price, last_sold_at
            FROM market_snapshot
            WHERE {" AND ".join(conditions)}
            ORDER BY snapshot_at DESC
            LIMIT %s
        """
        rows = conn.execute(sql, params).fetchall()
        return [
            SnapshotRow(
                region=r[0],
                snapshot_at=r[1],
                item_id=r[2],
                sid=r[3],
                base_price=r[4],
                current_stock=r[5],
                total_trades=r[6],
                last_sold_price=r[7],
                last_sold_at=r[8],
            )
            for r in rows
        ]

    @staticmethod
    def get_latest(
        conn: psycopg.Connection[tuple[Any, ...]],
        *,
        region: str,
        item_id: int,
        sid: int,
        limit: int = 1,
    ) -> list[SnapshotRow]:
        """Get the N most recent snapshots for an (item, sid)."""
        sql = """
            SELECT region, snapshot_at, item_id, sid, base_price, current_stock,
                   total_trades, last_sold_price, last_sold_at
            FROM market_snapshot
            WHERE region = %s AND item_id = %s AND sid = %s
            ORDER BY snapshot_at DESC
            LIMIT %s
        """
        rows = conn.execute(sql, (region, item_id, sid, limit)).fetchall()
        return [
            SnapshotRow(
                region=r[0],
                snapshot_at=r[1],
                item_id=r[2],
                sid=r[3],
                base_price=r[4],
                current_stock=r[5],
                total_trades=r[6],
                last_sold_price=r[7],
                last_sold_at=r[8],
            )
            for r in rows
        ]

    @staticmethod
    def purge_older_than(
        conn: psycopg.Connection[tuple[Any, ...]],
        cutoff: datetime,
    ) -> int:
        """Delete snapshots with ``snapshot_at < cutoff``. Returns rows deleted.

        Backs the daily retention sweep (FR-7); the ``(snapshot_at)`` index
        keeps the scan cheap.
        """
        cur = conn.cursor()
        cur.execute("DELETE FROM market_snapshot WHERE snapshot_at < %s", (cutoff,))
        return cur.rowcount


class DailyRepo:
    """market_daily table operations."""

    @staticmethod
    def upsert(conn: psycopg.Connection[tuple[Any, ...]], row: DailyRow) -> None:
        """INSERT ... ON CONFLICT (region, item_id, sid, trade_date) DO UPDATE."""
        sql = """
            INSERT INTO market_daily
                (region, trade_date, item_id, sid, open_price, high_price, low_price,
                 close_price, avg_price, total_trades_delta, avg_stock, snapshot_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (region, item_id, sid, trade_date) DO UPDATE SET
                open_price = EXCLUDED.open_price,
                high_price = EXCLUDED.high_price,
                low_price = EXCLUDED.low_price,
                close_price = EXCLUDED.close_price,
                avg_price = EXCLUDED.avg_price,
                total_trades_delta = EXCLUDED.total_trades_delta,
                avg_stock = EXCLUDED.avg_stock,
                snapshot_count = EXCLUDED.snapshot_count
        """
        conn.execute(
            sql,
            (
                row.region,
                row.trade_date,
                row.item_id,
                row.sid,
                row.open_price,
                row.high_price,
                row.low_price,
                row.close_price,
                row.avg_price,
                row.total_trades_delta,
                row.avg_stock,
                row.snapshot_count,
            ),
        )

    @staticmethod
    def rollup_day(
        conn: psycopg.Connection[tuple[Any, ...]],
        *,
        region: str,
        trade_date: date,
    ) -> int:
        """Aggregate one UTC day's snapshots into ``market_daily`` (idempotent).

        OHLC is computed on ``base_price`` (the canonical price): ``open``/
        ``close`` are the day's earliest/latest snapshot by ``snapshot_at``,
        ``high``/``low`` the extremes, ``avg_price`` the mean. ``total_trades_delta``
        is last-minus-first cumulative trades; ``avg_stock`` the mean stock;
        ``snapshot_count`` the number of snapshots. The aggregation runs entirely
        server-side over the half-open UTC day ``[trade_date, trade_date+1)`` and
        upserts on ``(region, item_id, sid, trade_date)``. Returns rows upserted.
        """
        day_start = datetime.combine(trade_date, time.min, tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        sql = """
            WITH s AS (
                SELECT item_id, sid, snapshot_at, base_price, current_stock, total_trades
                FROM market_snapshot
                WHERE region = %s AND snapshot_at >= %s AND snapshot_at < %s
            ),
            agg AS (
                SELECT
                    item_id,
                    sid,
                    (array_agg(base_price ORDER BY snapshot_at ASC))[1]   AS open_price,
                    MAX(base_price)                                        AS high_price,
                    MIN(base_price)                                        AS low_price,
                    (array_agg(base_price ORDER BY snapshot_at DESC))[1]  AS close_price,
                    AVG(base_price)::bigint                                AS avg_price,
                    (array_agg(total_trades ORDER BY snapshot_at DESC))[1]
                    - (array_agg(total_trades ORDER BY snapshot_at ASC))[1]
                    AS total_trades_delta,
                    AVG(current_stock)::int                                AS avg_stock,
                    COUNT(*)                                               AS snapshot_count
                FROM s
                GROUP BY item_id, sid
            )
            INSERT INTO market_daily (
                region, trade_date, item_id, sid, open_price, high_price, low_price,
                close_price, avg_price, total_trades_delta, avg_stock, snapshot_count
            )
            SELECT %s, %s, item_id, sid, open_price, high_price, low_price,
                   close_price, avg_price, total_trades_delta, avg_stock, snapshot_count
            FROM agg
            ON CONFLICT (region, item_id, sid, trade_date) DO UPDATE SET
                open_price = EXCLUDED.open_price,
                high_price = EXCLUDED.high_price,
                low_price = EXCLUDED.low_price,
                close_price = EXCLUDED.close_price,
                avg_price = EXCLUDED.avg_price,
                total_trades_delta = EXCLUDED.total_trades_delta,
                avg_stock = EXCLUDED.avg_stock,
                snapshot_count = EXCLUDED.snapshot_count
        """
        cur = conn.cursor()
        cur.execute(sql, (region, day_start, day_end, region, trade_date))
        return cur.rowcount

    @staticmethod
    def get_daily(
        conn: psycopg.Connection[tuple[Any, ...]],
        *,
        region: str,
        item_id: int,
        sid: int | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[DailyRow]:
        """SELECT daily rows with parameterized filters."""
        conditions = ["region = %s", "item_id = %s"]
        params: list[object] = [region, item_id]

        if sid is not None:
            conditions.append("sid = %s")
            params.append(sid)
        if from_date is not None:
            conditions.append("trade_date >= %s")
            params.append(from_date)
        if to_date is not None:
            conditions.append("trade_date <= %s")
            params.append(to_date)

        sql = f"""
            SELECT region, trade_date, item_id, sid, open_price, high_price,
                   low_price, close_price, avg_price, total_trades_delta,
                   avg_stock, snapshot_count
            FROM market_daily
            WHERE {" AND ".join(conditions)}
            ORDER BY trade_date DESC
        """
        rows = conn.execute(sql, params).fetchall()
        return [
            DailyRow(
                region=r[0],
                trade_date=r[1],
                item_id=r[2],
                sid=r[3],
                open_price=r[4],
                high_price=r[5],
                low_price=r[6],
                close_price=r[7],
                avg_price=r[8],
                total_trades_delta=r[9],
                avg_stock=r[10],
                snapshot_count=r[11],
            )
            for r in rows
        ]

    @staticmethod
    def get_daily_window(
        conn: psycopg.Connection[tuple[Any, ...]],
        *,
        region: str,
        item_id: int,
        sid: int,
        window_days: int = 14,
    ) -> list[DailyRow]:
        """Get the most recent N days of daily data for analytics."""
        sql = """
            SELECT region, trade_date, item_id, sid, open_price, high_price,
                   low_price, close_price, avg_price, total_trades_delta,
                   avg_stock, snapshot_count
            FROM market_daily
            WHERE region = %s AND item_id = %s AND sid = %s
            ORDER BY trade_date DESC
            LIMIT %s
        """
        rows = conn.execute(sql, (region, item_id, sid, window_days)).fetchall()
        return [
            DailyRow(
                region=r[0],
                trade_date=r[1],
                item_id=r[2],
                sid=r[3],
                open_price=r[4],
                high_price=r[5],
                low_price=r[6],
                close_price=r[7],
                avg_price=r[8],
                total_trades_delta=r[9],
                avg_stock=r[10],
                snapshot_count=r[11],
            )
            for r in rows
        ]
