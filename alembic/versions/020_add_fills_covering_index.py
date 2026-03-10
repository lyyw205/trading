"""Add covering index on fills for recompute_from_fills index-only scan

Revision ID: 020
Revises: 019
Create Date: 2026-03-10
"""

from alembic import op

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "idx_fills_recompute ON fills (account_id, symbol) "
        "INCLUDE (side, qty, quote_qty)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_fills_recompute")
