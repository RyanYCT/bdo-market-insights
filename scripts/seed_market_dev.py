"""Dev-only synthetic market backfill, so the insights pipeline has data to narrate.

`scripts/seed_items.py` only makes items *tracked* in DynamoDB; the insights
pipeline reads RDS `market_daily`, and `top_movers` needs a prior day (and ~7-14
days for volatility/anomaly). Since `insightsCompute` targets *yesterday*, a
single live ETL cycle can't produce a non-empty digest -- you'd wait days.

This inserts a small, deterministic synthetic dataset (a few buff + accessory
items over a 14-day window ending yesterday) shaped to exercise the narration:
a steady gainer, a steady loser, an anomalous spike, and an accessory whose
enhancement-cost ladder moves. It is **dev-only** and writes to whatever
DATABASE_URL points at -- run it over the bastion tunnel against the dev RDS.

Usage (with an open `make db-tunnel-up STAGE=dev` and a privileged DATABASE_URL):

    export DATABASE_URL="postgresql://USER:PW@localhost:5432/bdo"
    uv run python scripts/seed_market_dev.py            # backfill region tw
    uv run python scripts/seed_market_dev.py --dry-run  # preview row counts
    uv run python scripts/seed_market_dev.py --clean     # remove synthetic rows

Synthetic items use IDs >= 90_000_000 so they never collide with real arsha.io
item IDs and cleanup is a simple range delete.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime, timedelta

import psycopg

#: Synthetic items live at/above this ID so they can't collide with real arsha
#: item IDs and `--clean` can range-delete them safely.
SYNTH_ID_MIN = 90_000_000

#: Trailing window to backfill (>= analytics MIN_POINTS so anomaly/volatility work).
DAYS = 14


def _normalise_dsn(url: str) -> str:
    """Accept the SQLAlchemy-style ``postgresql+psycopg://`` URL used elsewhere."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _ohlc(close: int, prev_close: int) -> dict[str, int]:
    """Plausible OHLC around a close (synthetic; not used by the digest math)."""
    return {
        "open_price": prev_close,
        "high_price": int(close * 1.03),
        "low_price": int(close * 0.97),
        "close_price": close,
        "avg_price": int(close * 0.99),
    }


def _gainer(base: int) -> list[int]:
    """Smooth uptrend, then a clear +6% on the final day."""
    closes = [round(base * (1.008**d)) for d in range(DAYS)]
    closes[-1] = round(closes[-2] * 1.06)
    return closes


def _loser(base: int) -> list[int]:
    """Smooth downtrend, then a clear -7.5% on the final day."""
    closes = [round(base * (0.99**d)) for d in range(DAYS)]
    closes[-1] = round(closes[-2] * 0.925)
    return closes


def _spike(level: int) -> list[int]:
    """Flat history, then a +35% final-day spike -> anomaly (|z| > 3)."""
    closes = [level] * DAYS
    closes[-1] = round(level * 1.35)
    return closes


def _drift(base: int, *, final_mult: float = 1.0, rate: float = 1.003) -> list[int]:
    """Gentle drift, optionally with a deliberate final-day move."""
    closes = [round(base * (rate**d)) for d in range(DAYS)]
    closes[-1] = round(closes[-2] * final_mult)
    return closes


#: (item_id, name, category, sid, total_trades_delta, close-series)
def _series(end_date: date) -> list[tuple[int, str, str, int, int, list[int]]]:
    return [
        (SYNTH_ID_MIN + 1, "Elixir of Frenzy", "buff", 0, 800, _gainer(1_000_000)),
        (SYNTH_ID_MIN + 2, "Tears of Elion", "buff", 0, 350, _loser(2_000_000)),
        # Highest volume + an anomalous final-day spike: should lead the headline.
        (SYNTH_ID_MIN + 3, "Surge Powder", "buff", 0, 1500, _spike(500_000)),
        # Accessory ladder (sids 0..3); sid 3 drops -12% (top loser). The clean
        # (sid 0) price rises +5%, which drives the enhancement_cost_change.
        (
            SYNTH_ID_MIN + 4,
            "Ring of Cadry",
            "accessory",
            0,
            200,
            _drift(100_000_000, final_mult=1.05),
        ),
        (SYNTH_ID_MIN + 4, "Ring of Cadry", "accessory", 1, 180, _drift(250_000_000)),
        (SYNTH_ID_MIN + 4, "Ring of Cadry", "accessory", 2, 150, _drift(600_000_000)),
        (
            SYNTH_ID_MIN + 4,
            "Ring of Cadry",
            "accessory",
            3,
            600,
            _drift(1_200_000_000, final_mult=0.88),
        ),
    ]


def _items(series: list[tuple[int, str, str, int, int, list[int]]]) -> dict[int, tuple[str, str]]:
    """Unique {item_id: (name, category)} from the series."""
    return {row[0]: (row[1], row[2]) for row in series}


def clean(conn: psycopg.Connection[tuple[object, ...]], region: str) -> None:
    """Delete all synthetic rows (FK order: market_daily -> item_sid -> item)."""
    conn.execute(
        "DELETE FROM market_daily WHERE region = %s AND item_id >= %s",
        (region, SYNTH_ID_MIN),
    )
    conn.execute(
        "DELETE FROM item_sid WHERE region = %s AND item_id >= %s",
        (region, SYNTH_ID_MIN),
    )
    conn.execute("DELETE FROM item WHERE id >= %s", (SYNTH_ID_MIN,))


def seed(
    conn: psycopg.Connection[tuple[object, ...]], region: str, end_date: date, *, dry_run: bool
) -> None:
    series = _series(end_date)
    items = _items(series)
    dates = [end_date - timedelta(days=DAYS - 1 - i) for i in range(DAYS)]
    daily_rows = len(series) * DAYS

    if dry_run:
        print(f"[DRY RUN] region={region} window={dates[0]}..{dates[-1]} ({DAYS} days)")
        print(
            f"[DRY RUN] would write {len(items)} items, {len(series)} item_sid rows, "
            f"{daily_rows} market_daily rows"
        )
        for item_id, (name, category) in sorted(items.items()):
            print(f"[DRY RUN]   item {item_id} {name!r} ({category})")
        return

    # Fresh slate so re-runs are deterministic.
    clean(conn, region)

    for item_id, (name, category) in items.items():
        conn.execute(
            "INSERT INTO item (id, name, category) VALUES (%s, %s, %s)",
            (item_id, name, category),
        )

    for item_id, _name, _category, sid, trades, closes in series:
        max_sid = max(r[3] for r in series if r[0] == item_id)
        conn.execute(
            """
            INSERT INTO item_sid (region, item_id, sid, max_enhance, price_min, price_max)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (region, item_id, sid) DO NOTHING
            """,
            (region, item_id, sid, max_sid, 1, 1_000_000_000_000),
        )
        for i, trade_date in enumerate(dates):
            close = closes[i]
            prev_close = closes[i - 1] if i > 0 else close
            ohlc = _ohlc(close, prev_close)
            conn.execute(
                """
                INSERT INTO market_daily
                    (region, trade_date, item_id, sid, open_price, high_price, low_price,
                     close_price, avg_price, total_trades_delta, avg_stock, snapshot_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    region,
                    trade_date,
                    item_id,
                    sid,
                    ohlc["open_price"],
                    ohlc["high_price"],
                    ohlc["low_price"],
                    ohlc["close_price"],
                    ohlc["avg_price"],
                    trades,
                    100,
                    24,
                ),
            )

    print(
        f"Seeded region={region} window={dates[0]}..{dates[-1]}: "
        f"{len(items)} items, {len(series)} item_sid rows, {daily_rows} market_daily rows."
    )
    print("insightsCompute targets yesterday, so run insights for period=daily and weekly next.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="tw", help="BDO region (default: tw)")
    parser.add_argument(
        "--end-date",
        default=None,
        help="Last day to seed (YYYY-MM-DD). Default: yesterday UTC (insightsCompute's target).",
    )
    parser.add_argument("--clean", action="store_true", help="Delete synthetic rows and exit")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without writing")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: set DATABASE_URL (e.g. postgresql://USER:PW@localhost:5432/bdo)")
        sys.exit(1)

    end_date = (
        date.fromisoformat(args.end_date)
        if args.end_date
        else (datetime.now(tz=UTC) - timedelta(days=1)).date()
    )

    with psycopg.connect(_normalise_dsn(dsn)) as conn:
        if args.clean:
            if args.dry_run:
                print(
                    f"[DRY RUN] would delete synthetic rows id>={SYNTH_ID_MIN} "
                    f"in region {args.region}"
                )
            else:
                clean(conn, args.region)
                conn.commit()
                print(f"Removed synthetic rows (id >= {SYNTH_ID_MIN}) in region {args.region}.")
            return
        seed(conn, args.region, end_date, dry_run=args.dry_run)
        if not args.dry_run:
            conn.commit()


if __name__ == "__main__":
    main()
