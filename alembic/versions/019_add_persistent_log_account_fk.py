"""Add FK on persistent_logs.account_id, change health_score to NUMERIC(5,2)

Revision ID: 019
Revises: 018
Create Date: 2026-03-09
"""
import sqlalchemy as sa

from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_persistent_log_account",
        "persistent_logs",
        "trading_accounts",
        ["account_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.alter_column(
        "daily_reports",
        "health_score",
        type_=sa.Numeric(5, 2),
        existing_type=sa.Float(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "daily_reports",
        "health_score",
        type_=sa.Float(),
        existing_type=sa.Numeric(5, 2),
        existing_nullable=False,
    )
    op.drop_constraint("fk_persistent_log_account", "persistent_logs", type_="foreignkey")
