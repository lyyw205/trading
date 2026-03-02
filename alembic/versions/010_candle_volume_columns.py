"""Add volume, quote_volume, trade_count to price_candles_5m

Revision ID: 010
Revises: 009
Create Date: 2026-03-02
"""
from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op: these columns were already added in migration 008.
    pass


def downgrade() -> None:
    # No-op: columns are managed by migration 008.
    pass
