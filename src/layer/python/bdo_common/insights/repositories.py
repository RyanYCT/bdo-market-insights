"""Insight repositories: InsightRepo + SummaryRepo. Parameterized SQL, no ORM."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from bdo_common.insights.models import MarketDigest, MarketSummary, Narrative

if TYPE_CHECKING:
    import psycopg


class InsightRepo:
    """Read-side queries for digest construction."""

    @staticmethod
    def top_movers(
        conn: psycopg.Connection[tuple[Any, ...]],
        *,
        region: str,
        category: str,
        period: str,
        target_date: date,
        limit: int = 5,
    ) -> list[tuple[int, str, int, int, int, float, int]]:
        """Top movers by |pct_change| for a category.

        Joins item (filtered by category) with market_daily. For daily period,
        compares close on target_date to close on the prior available day. For
        weekly period, compares close on target_date to close 7 days prior.

        Returns list of (item_id, item_name, sid, close_price, prev_close_price,
        pct_change, total_trades_delta) ordered by |pct_change| DESC.
        """
        if period not in ("daily", "weekly"):
            raise ValueError(f"invalid period: {period!r}, must be 'daily' or 'weekly'")

        if period == "daily":
            sql = """
                WITH latest AS (
                    SELECT d.item_id, d.sid, d.close_price, d.total_trades_delta
                    FROM market_daily d
                    JOIN item i ON i.id = d.item_id
                    WHERE d.region = %s
                      AND d.trade_date = %s
                      AND i.category = %s
                ),
                prior AS (
                    SELECT DISTINCT ON (d.item_id, d.sid)
                           d.item_id, d.sid, d.close_price AS prev_close_price
                    FROM market_daily d
                    JOIN item i ON i.id = d.item_id
                    WHERE d.region = %s
                      AND d.trade_date < %s
                      AND i.category = %s
                    ORDER BY d.item_id, d.sid, d.trade_date DESC
                )
                SELECT l.item_id, i.name, l.sid, l.close_price,
                       p.prev_close_price,
                       CASE WHEN p.prev_close_price = 0 THEN 0.0
                            ELSE (l.close_price - p.prev_close_price)::float
                                 / p.prev_close_price * 100.0
                       END AS pct_change,
                       l.total_trades_delta
                FROM latest l
                JOIN prior p ON l.item_id = p.item_id AND l.sid = p.sid
                JOIN item i ON i.id = l.item_id
                ORDER BY ABS(
                    CASE WHEN p.prev_close_price = 0 THEN 0.0
                         ELSE (l.close_price - p.prev_close_price)::float
                              / p.prev_close_price * 100.0
                    END
                ) DESC
                LIMIT %s
            """
            params: list[object] = [
                region,
                target_date,
                category,
                region,
                target_date,
                category,
                limit,
            ]
        else:
            # weekly: compare target_date close to most recent row at least 7 days prior
            sql = """
                WITH latest AS (
                    SELECT d.item_id, d.sid, d.close_price, d.total_trades_delta
                    FROM market_daily d
                    JOIN item i ON i.id = d.item_id
                    WHERE d.region = %s
                      AND d.trade_date = %s
                      AND i.category = %s
                ),
                prior AS (
                    SELECT DISTINCT ON (d.item_id, d.sid)
                           d.item_id, d.sid, d.close_price AS prev_close_price
                    FROM market_daily d
                    JOIN item i ON i.id = d.item_id
                    WHERE d.region = %s
                      AND d.trade_date <= %s - INTERVAL '7 days'
                      AND i.category = %s
                    ORDER BY d.item_id, d.sid, d.trade_date DESC
                )
                SELECT l.item_id, i.name, l.sid, l.close_price,
                       p.prev_close_price,
                       CASE WHEN p.prev_close_price = 0 THEN 0.0
                            ELSE (l.close_price - p.prev_close_price)::float
                                 / p.prev_close_price * 100.0
                       END AS pct_change,
                       l.total_trades_delta
                FROM latest l
                JOIN prior p ON l.item_id = p.item_id AND l.sid = p.sid
                JOIN item i ON i.id = l.item_id
                ORDER BY ABS(
                    CASE WHEN p.prev_close_price = 0 THEN 0.0
                         ELSE (l.close_price - p.prev_close_price)::float
                              / p.prev_close_price * 100.0
                    END
                ) DESC
                LIMIT %s
            """
            params = [
                region,
                target_date,
                category,
                region,
                target_date,
                category,
                limit,
            ]

        rows = conn.execute(sql, params).fetchall()
        return [
            (
                int(r[0]),
                str(r[1]),
                int(r[2]),
                int(r[3]),
                int(r[4]),
                float(r[5]),
                int(r[6]),
            )
            for r in rows
        ]


class SummaryRepo:
    """market_summary table operations."""

    @staticmethod
    def upsert(
        conn: psycopg.Connection[tuple[Any, ...]],
        *,
        region: str,
        period: str,
        summary_date: date,
        lang: str,
        model_id: str,
        digest: MarketDigest,
        narrative: Narrative,
    ) -> None:
        """INSERT ... ON CONFLICT (region, period, summary_date, lang) DO UPDATE."""
        sql = """
            INSERT INTO market_summary
                (region, period, summary_date, lang, model_id, digest, narrative)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
            ON CONFLICT (region, period, summary_date, lang) DO UPDATE SET
                model_id = EXCLUDED.model_id,
                digest = EXCLUDED.digest,
                narrative = EXCLUDED.narrative,
                created_at = NOW()
        """
        conn.execute(
            sql,
            (
                region,
                period,
                summary_date,
                lang,
                model_id,
                digest.model_dump_json(),
                narrative.model_dump_json(),
            ),
        )

    @staticmethod
    def get(
        conn: psycopg.Connection[tuple[Any, ...]],
        *,
        region: str,
        period: str,
        summary_date: date | None = None,
        lang: str = "en",
    ) -> MarketSummary | None:
        """Fetch a single summary. If date is None, returns the latest."""
        if summary_date is not None:
            sql = """
                SELECT region, period, summary_date, lang, model_id,
                       digest, narrative, created_at
                FROM market_summary
                WHERE region = %s AND period = %s
                      AND summary_date = %s AND lang = %s
            """
            params: list[object] = [region, period, summary_date, lang]
        else:
            sql = """
                SELECT region, period, summary_date, lang, model_id,
                       digest, narrative, created_at
                FROM market_summary
                WHERE region = %s AND period = %s AND lang = %s
                ORDER BY summary_date DESC
                LIMIT 1
            """
            params = [region, period, lang]

        row = conn.execute(sql, params).fetchone()
        if row is None:
            return None

        return MarketSummary(
            region=row[0],
            period=row[1],
            summary_date=row[2],
            lang=row[3],
            model_id=row[4],
            digest=_parse_jsonb(row[5], MarketDigest),
            narrative=_parse_jsonb(row[6], Narrative),
            created_at=_ensure_datetime(row[7]),
        )


def _parse_jsonb(value: Any, model: type[MarketDigest] | type[Narrative]) -> Any:
    """Parse a JSONB column that may arrive as dict or str."""
    import json as _json

    if isinstance(value, str):
        return model.model_validate_json(value)
    if isinstance(value, dict):
        return model.model_validate(value)
    # psycopg may return Json wrapper; try dict conversion
    data: dict[str, Any] = _json.loads(str(value))
    return model.model_validate(data)


def _ensure_datetime(value: Any) -> datetime:
    """Ensure a value is a datetime (handles edge cases from psycopg)."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
