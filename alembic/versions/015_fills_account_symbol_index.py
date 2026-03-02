"""Add composite index on fills(account_id, symbol) for recompute_from_fills

Revision ID: 015
Revises: 014
Create Date: 2026-03-02
"""
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_fills_account_symbol",
        "fills",
        ["account_id", "symbol"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("idx_fills_account_symbol", table_name="fills", if_exists=True)
