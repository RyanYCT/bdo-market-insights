"""Create initial schema: item, item_sid, market_snapshot, market_daily.

Revision ID: 0001
Revises: None
Create Date: 2025-01-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "item",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=True),
        sa.Column("main_category", sa.Text, nullable=True),
        sa.Column("sub_category", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "item_sid",
        sa.Column("region", sa.String(16), nullable=False),
        sa.Column("item_id", sa.Integer, nullable=False),
        sa.Column("sid", sa.SmallInteger, nullable=False),
        sa.Column("max_enhance", sa.SmallInteger, nullable=False),
        sa.Column("price_min", sa.BigInteger, nullable=False),
        sa.Column("price_max", sa.BigInteger, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("region", "item_id", "sid"),
        sa.ForeignKeyConstraint(["item_id"], ["item.id"]),
    )

    op.create_table(
        "market_snapshot",
        sa.Column("region", sa.String(16), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("item_id", sa.Integer, nullable=False),
        sa.Column("sid", sa.SmallInteger, nullable=False),
        sa.Column("base_price", sa.BigInteger, nullable=False),
        sa.Column("current_stock", sa.Integer, nullable=False),
        sa.Column("total_trades", sa.BigInteger, nullable=False),
        sa.Column("last_sold_price", sa.BigInteger, nullable=False),
        sa.Column("last_sold_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("region", "item_id", "sid", "snapshot_at"),
        sa.ForeignKeyConstraint(
            ["region", "item_id", "sid"],
            ["item_sid.region", "item_sid.item_id", "item_sid.sid"],
        ),
    )

    op.create_index(
        "ix_market_snapshot_snapshot_at",
        "market_snapshot",
        ["snapshot_at"],
    )
    op.create_index(
        "ix_market_snapshot_region_item_snapshot",
        "market_snapshot",
        [sa.text("region"), sa.text("item_id"), sa.text("snapshot_at DESC")],
    )

    op.create_table(
        "market_daily",
        sa.Column("region", sa.String(16), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("item_id", sa.Integer, nullable=False),
        sa.Column("sid", sa.SmallInteger, nullable=False),
        sa.Column("open_price", sa.BigInteger, nullable=False),
        sa.Column("high_price", sa.BigInteger, nullable=False),
        sa.Column("low_price", sa.BigInteger, nullable=False),
        sa.Column("close_price", sa.BigInteger, nullable=False),
        sa.Column("avg_price", sa.BigInteger, nullable=False),
        sa.Column("total_trades_delta", sa.BigInteger, nullable=False),
        sa.Column("avg_stock", sa.Integer, nullable=False),
        sa.Column("snapshot_count", sa.SmallInteger, nullable=False),
        sa.PrimaryKeyConstraint("region", "item_id", "sid", "trade_date"),
        sa.ForeignKeyConstraint(
            ["region", "item_id", "sid"],
            ["item_sid.region", "item_sid.item_id", "item_sid.sid"],
        ),
    )

    op.create_index(
        "ix_market_daily_region_item_date",
        "market_daily",
        [sa.text("region"), sa.text("item_id"), sa.text("trade_date DESC")],
    )


def downgrade() -> None:
    op.drop_table("market_daily")
    op.drop_table("market_snapshot")
    op.drop_table("item_sid")
    op.drop_table("item")
