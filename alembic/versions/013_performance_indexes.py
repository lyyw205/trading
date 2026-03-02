"""Add performance indexes for hot query paths

Revision ID: 013
Revises: 012
Create Date: 2026-03-02
"""
from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_fills_account_symbol",
        "fills",
        ["account_id", "symbol"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_lots_combo_v2",
        "lots",
        ["account_id", "combo_id", "symbol", "status"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_orders_open",
        "orders",
        ["account_id", "status"],
        postgresql_where="status IN ('NEW', 'PARTIALLY_FILLED')",
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("idx_orders_open", table_name="orders", if_exists=True)
    op.drop_index("idx_lots_combo_v2", table_name="lots", if_exists=True)
    op.drop_index("idx_fills_account_symbol", table_name="fills", if_exists=True)
