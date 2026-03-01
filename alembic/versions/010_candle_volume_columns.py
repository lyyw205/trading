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
    for table in ("price_candles_5m",):
        op.add_column(table, sa.Column("volume", sa.Numeric(), nullable=False, server_default="0"))
        op.add_column(table, sa.Column("quote_volume", sa.Numeric(), nullable=False, server_default="0"))
        op.add_column(table, sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    for table in ("price_candles_5m",):
        op.drop_column(table, "trade_count")
        op.drop_column(table, "quote_volume")
        op.drop_column(table, "volume")
