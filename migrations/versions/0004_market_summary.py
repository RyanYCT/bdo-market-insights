"""Create market_summary table for LLM-generated insights.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-14 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_summary",
        sa.Column("region", sa.String(16), nullable=False),
        sa.Column("period", sa.String(8), nullable=False),
        sa.Column("summary_date", sa.Date, nullable=False),
        sa.Column("lang", sa.String(8), nullable=False, server_default="en"),
        sa.Column("model_id", sa.Text, nullable=False),
        sa.Column("digest", postgresql.JSONB, nullable=False),
        sa.Column("narrative", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("region", "period", "summary_date", "lang"),
    )

    op.create_index(
        "ix_market_summary_region_period_date",
        "market_summary",
        [sa.text("region"), sa.text("period"), sa.text("summary_date DESC")],
    )


def downgrade() -> None:
    op.drop_table("market_summary")
