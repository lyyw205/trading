"""Add discord_sent_at column to daily_reports

Revision ID: 023
Revises: 022
Create Date: 2026-03-16
"""

import sqlalchemy as sa

from alembic import op

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("daily_reports", sa.Column("discord_sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("daily_reports", "discord_sent_at")
