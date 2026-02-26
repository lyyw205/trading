"""Add trading_combos table and lots.combo_id column

Revision ID: 004
Revises: 003
Create Date: 2026-02-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create trading_combos table
    op.create_table(
        "trading_combos",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("buy_logic_name", sa.String(), nullable=False),
        sa.Column("buy_params", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("sell_logic_name", sa.String(), nullable=False),
        sa.Column("sell_params", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("reference_combo_id", sa.Uuid(), sa.ForeignKey("trading_combos.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("reference_combo_id != id", name="chk_no_self_reference"),
    )
    op.create_index("idx_combos_account", "trading_combos", ["account_id", "is_enabled"])

    # 2. Add combo_id column to lots table
    op.add_column("lots", sa.Column("combo_id", sa.Uuid(), sa.ForeignKey("trading_combos.id"), nullable=True))
    op.create_index("idx_lots_combo", "lots", ["account_id", "combo_id", "status"])


def downgrade() -> None:
    op.drop_index("idx_lots_combo", table_name="lots")
    op.drop_column("lots", "combo_id")
    op.drop_index("idx_combos_account", table_name="trading_combos")
    op.drop_table("trading_combos")
