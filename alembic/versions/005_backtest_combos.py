"""Add combos JSONB to backtest_runs, drop strategy_account_access

Revision ID: 005
Revises: 004
Create Date: 2026-02-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add combos column to backtest_runs
    op.add_column(
        "backtest_runs",
        sa.Column("combos", postgresql.JSONB(), nullable=True),
    )

    # 2. Make legacy columns nullable
    op.alter_column("backtest_runs", "strategies", nullable=True)
    op.alter_column("backtest_runs", "strategy_params", nullable=True)

    # 3. Drop strategy_account_access table
    op.drop_table("strategy_account_access")


def downgrade() -> None:
    # 1. Re-create strategy_account_access
    op.create_table(
        "strategy_account_access",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("strategy_name", sa.String(), nullable=False),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=True),
        sa.Column("granted_by", sa.Uuid(), sa.ForeignKey("user_profiles.id"), nullable=True),
        sa.UniqueConstraint("strategy_name", "account_id", name="uq_strategy_account_access"),
    )

    # 2. Restore non-nullable constraints
    op.alter_column("backtest_runs", "strategies", nullable=False)
    op.alter_column("backtest_runs", "strategy_params", nullable=False)

    # 3. Drop combos column
    op.drop_column("backtest_runs", "combos")
