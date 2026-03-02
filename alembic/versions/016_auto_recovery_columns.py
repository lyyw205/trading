"""Add auto_recovery_attempts and last_auto_recovery_at to trading_accounts

Revision ID: 016
Revises: 015
Create Date: 2026-03-03
"""
import sqlalchemy as sa

from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trading_accounts", sa.Column("auto_recovery_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("trading_accounts", sa.Column("last_auto_recovery_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("trading_accounts", "last_auto_recovery_at")
    op.drop_column("trading_accounts", "auto_recovery_attempts")
