"""Add backtest_runs table

Revision ID: 002_backtest_runs
Revises: 001_initial
Create Date: 2026-02-25
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002_backtest_runs"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("user_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("strategies", postgresql.JSONB(), nullable=False),
        sa.Column(
            "strategy_params",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("initial_usdt", sa.Numeric(), nullable=False),
        sa.Column("start_ts_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ts_ms", sa.BigInteger(), nullable=False),
        sa.Column(
            "status", sa.String(), nullable=False, server_default="PENDING"
        ),
        sa.Column("result_summary", postgresql.JSONB(), nullable=True),
        sa.Column("trade_log", postgresql.JSONB(), nullable=True),
        sa.Column("equity_curve", postgresql.JSONB(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_backtest_runs_user", "backtest_runs", ["user_id"]
    )
    op.create_index(
        "idx_backtest_runs_status", "backtest_runs", ["status"]
    )


def downgrade() -> None:
    op.drop_index("idx_backtest_runs_status")
    op.drop_index("idx_backtest_runs_user")
    op.drop_table("backtest_runs")
