"""Candle tables - add 1m/1h/1d tables and volume columns to 5m

Revision ID: 008
Revises: 007
Create Date: 2026-02-26
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add volume columns to existing price_candles_5m table
    op.add_column(
        "price_candles_5m",
        sa.Column("volume", sa.Numeric(), nullable=False, server_default="0"),
    )
    op.add_column(
        "price_candles_5m",
        sa.Column("quote_volume", sa.Numeric(), nullable=False, server_default="0"),
    )
    op.add_column(
        "price_candles_5m",
        sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"),
    )

    # Create price_candles_1m table
    op.create_table(
        "price_candles_1m",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("ts_ms", sa.BigInteger(), nullable=False),
        sa.Column("open", sa.Numeric(), nullable=False),
        sa.Column("high", sa.Numeric(), nullable=False),
        sa.Column("low", sa.Numeric(), nullable=False),
        sa.Column("close", sa.Numeric(), nullable=False),
        sa.Column("volume", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("quote_volume", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_price_candles_1m_symbol_ts",
        "price_candles_1m",
        ["symbol", "ts_ms"],
        unique=True,
    )

    # Create price_candles_1h table
    op.create_table(
        "price_candles_1h",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("ts_ms", sa.BigInteger(), nullable=False),
        sa.Column("open", sa.Numeric(), nullable=False),
        sa.Column("high", sa.Numeric(), nullable=False),
        sa.Column("low", sa.Numeric(), nullable=False),
        sa.Column("close", sa.Numeric(), nullable=False),
        sa.Column("volume", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("quote_volume", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_price_candles_1h_symbol_ts",
        "price_candles_1h",
        ["symbol", "ts_ms"],
        unique=True,
    )

    # Create price_candles_1d table
    op.create_table(
        "price_candles_1d",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("ts_ms", sa.BigInteger(), nullable=False),
        sa.Column("open", sa.Numeric(), nullable=False),
        sa.Column("high", sa.Numeric(), nullable=False),
        sa.Column("low", sa.Numeric(), nullable=False),
        sa.Column("close", sa.Numeric(), nullable=False),
        sa.Column("volume", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("quote_volume", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_price_candles_1d_symbol_ts",
        "price_candles_1d",
        ["symbol", "ts_ms"],
        unique=True,
    )


def downgrade() -> None:
    # Drop new tables
    op.drop_index("idx_price_candles_1d_symbol_ts", table_name="price_candles_1d")
    op.drop_table("price_candles_1d")
    op.drop_index("idx_price_candles_1h_symbol_ts", table_name="price_candles_1h")
    op.drop_table("price_candles_1h")
    op.drop_index("idx_price_candles_1m_symbol_ts", table_name="price_candles_1m")
    op.drop_table("price_candles_1m")

    # Remove added columns from price_candles_5m
    op.drop_column("price_candles_5m", "trade_count")
    op.drop_column("price_candles_5m", "quote_volume")
    op.drop_column("price_candles_5m", "volume")
