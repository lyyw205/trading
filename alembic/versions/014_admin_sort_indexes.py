"""Add indexes for admin page sorting performance

Revision ID: 014
Revises: 013
Create Date: 2026-03-02
"""
from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_orders_update_time",
        "orders",
        ["update_time_ms"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_lots_buy_time",
        "lots",
        ["buy_time"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_fills_inserted_at",
        "fills",
        ["inserted_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("idx_fills_inserted_at", table_name="fills", if_exists=True)
    op.drop_index("idx_lots_buy_time", table_name="lots", if_exists=True)
    op.drop_index("idx_orders_update_time", table_name="orders", if_exists=True)
