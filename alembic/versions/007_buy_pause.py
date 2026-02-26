"""Buy pause - state columns for low-balance throttle/pause

Revision ID: 007
Revises: 006
Create Date: 2026-02-26
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "trading_accounts",
        sa.Column("buy_pause_state", sa.String(20), nullable=False, server_default="ACTIVE"),
    )
    op.add_column(
        "trading_accounts",
        sa.Column("buy_pause_reason", sa.String(50), nullable=True),
    )
    op.add_column(
        "trading_accounts",
        sa.Column("buy_pause_since", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "trading_accounts",
        sa.Column("consecutive_low_balance", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("trading_accounts", "consecutive_low_balance")
    op.drop_column("trading_accounts", "buy_pause_since")
    op.drop_column("trading_accounts", "buy_pause_reason")
    op.drop_column("trading_accounts", "buy_pause_state")
