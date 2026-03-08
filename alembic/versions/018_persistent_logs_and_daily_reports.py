"""Create persistent_logs and daily_reports tables

Revision ID: 018
Revises: 017
Create Date: 2026-03-08
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "persistent_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("account_id", UUID(as_uuid=True), nullable=True),
        sa.Column("module", sa.String(100), nullable=True),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("exception", sa.Text, nullable=True),
        sa.Column("extra", JSONB, nullable=True),
    )
    op.create_index(
        "ix_persistent_log_level_logged",
        "persistent_logs",
        ["level", "logged_at"],
    )
    op.create_index(
        "ix_persistent_log_account_logged",
        "persistent_logs",
        ["account_id", "logged_at"],
        postgresql_where=sa.text("account_id IS NOT NULL"),
    )
    op.create_index(
        "ix_persistent_log_logged",
        "persistent_logs",
        ["logged_at"],
    )

    op.create_table(
        "daily_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("report_date", sa.Date, nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("health_score", sa.Float, nullable=False),
        sa.Column("summary", JSONB, nullable=False),
        sa.Column("telegram_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("report_date", name="uq_daily_report_date"),
    )


def downgrade() -> None:
    op.drop_table("daily_reports")
    op.drop_index("ix_persistent_log_logged", table_name="persistent_logs")
    op.drop_index("ix_persistent_log_account_logged", table_name="persistent_logs")
    op.drop_index("ix_persistent_log_level_logged", table_name="persistent_logs")
    op.drop_table("persistent_logs")
