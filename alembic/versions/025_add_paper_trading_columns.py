"""Add paper trading columns (is_paper, paper_initial_balance)

Revision ID: 025
Revises: 024
Create Date: 2026-04-03
"""

import sqlalchemy as sa

from alembic import op

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trading_accounts",
        sa.Column("is_paper", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "trading_accounts",
        sa.Column("paper_initial_balance", sa.Numeric(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("trading_accounts", "paper_initial_balance")
    op.drop_column("trading_accounts", "is_paper")
