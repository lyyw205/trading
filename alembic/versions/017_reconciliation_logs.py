"""Create reconciliation_logs table

Revision ID: 017
Revises: 016
Create Date: 2026-03-03
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("position_diffs", JSONB, nullable=True),
        sa.Column("balance_diff", JSONB, nullable=True),
        sa.Column("fill_gaps", JSONB, nullable=True),
        sa.Column("auto_resolved", sa.Boolean, server_default="false"),
    )
    op.create_index(
        "idx_recon_account_checked",
        "reconciliation_logs",
        ["account_id", sa.text("checked_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_recon_account_checked", table_name="reconciliation_logs")
    op.drop_table("reconciliation_logs")
