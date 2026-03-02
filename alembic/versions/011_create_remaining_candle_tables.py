"""Create price_candles_1m, 1h, 1d tables

Revision ID: 011
Revises: 010
Create Date: 2026-03-02
"""
from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op: these tables were already created in migration 008.
    pass


def downgrade() -> None:
    # No-op: tables are managed by migration 008.
    pass
