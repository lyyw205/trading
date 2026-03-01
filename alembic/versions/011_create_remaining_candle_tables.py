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
    for table, idx in [
        ("price_candles_1m", "idx_price_candles_1m_symbol_ts"),
        ("price_candles_1h", "idx_price_candles_1h_symbol_ts"),
        ("price_candles_1d", "idx_price_candles_1d_symbol_ts"),
    ]:
        op.create_table(
            table,
            sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
            sa.Column("symbol", sa.String(), nullable=False),
            sa.Column("ts_ms", sa.BigInteger(), nullable=False),
            sa.Column("open", sa.Numeric(), nullable=False),
            sa.Column("high", sa.Numeric(), nullable=False),
            sa.Column("low", sa.Numeric(), nullable=False),
            sa.Column("close", sa.Numeric(), nullable=False),
            sa.Column("volume", sa.Numeric(), nullable=False, server_default="0"),
            sa.Column("quote_volume", sa.Numeric(), nullable=False, server_default="0"),
            sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index(idx, table, ["symbol", "ts_ms"], unique=True)


def downgrade() -> None:
    for table in ("price_candles_1d", "price_candles_1h", "price_candles_1m"):
        op.drop_table(table)
