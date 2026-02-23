"""Initial schema

Revision ID: 001_initial
Revises:
Create Date: 2026-02-23
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # user_profiles
    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=False, server_default="user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # trading_accounts
    op.create_table(
        "trading_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("exchange", sa.String(), nullable=False, server_default="binance"),
        sa.Column("symbol", sa.String(), nullable=False, server_default="ETHUSDT"),
        sa.Column("base_asset", sa.String(), nullable=False, server_default="ETH"),
        sa.Column("quote_asset", sa.String(), nullable=False, server_default="USDT"),
        sa.Column("api_key_encrypted", sa.String(), nullable=False),
        sa.Column("api_secret_encrypted", sa.String(), nullable=False),
        sa.Column("encryption_key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("circuit_breaker_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("circuit_breaker_disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("loop_interval_sec", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("order_cooldown_sec", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_trading_accounts_owner", "trading_accounts", ["owner_id"])

    # strategy_configs
    op.create_table(
        "strategy_configs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_name", sa.String(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default="true"),
        sa.Column("params", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("account_id", "strategy_name", name="uq_strategy_per_account"),
    )

    # strategy_state
    op.create_table(
        "strategy_state",
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("trading_accounts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("scope", sa.String(), primary_key=True),
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_strategy_state_account", "strategy_state", ["account_id"])

    # orders
    op.create_table(
        "orders",
        sa.Column("order_id", sa.BigInteger(), primary_key=True),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("trading_accounts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=True),
        sa.Column("type", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("price", sa.Numeric(), nullable=True),
        sa.Column("orig_qty", sa.Numeric(), nullable=True),
        sa.Column("executed_qty", sa.Numeric(), nullable=True),
        sa.Column("cum_quote_qty", sa.Numeric(), nullable=True),
        sa.Column("client_order_id", sa.String(), nullable=True),
        sa.Column("update_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_orders_status", "orders", ["account_id", "status"])

    # fills
    op.create_table(
        "fills",
        sa.Column("trade_id", sa.BigInteger(), primary_key=True),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("trading_accounts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=True),
        sa.Column("price", sa.Numeric(), nullable=True),
        sa.Column("qty", sa.Numeric(), nullable=True),
        sa.Column("quote_qty", sa.Numeric(), nullable=True),
        sa.Column("commission", sa.Numeric(), nullable=True),
        sa.Column("commission_asset", sa.String(), nullable=True),
        sa.Column("trade_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(), nullable=True),
        sa.Column("inserted_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # lots
    op.create_table(
        "lots",
        sa.Column("lot_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("trading_accounts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("strategy_name", sa.String(), nullable=False, server_default="lot_stacking"),
        sa.Column("buy_order_id", sa.BigInteger(), nullable=True),
        sa.Column("buy_price", sa.Numeric(), nullable=False),
        sa.Column("buy_qty", sa.Numeric(), nullable=False),
        sa.Column("buy_time", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("buy_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(), server_default="OPEN"),
        sa.Column("sell_order_id", sa.BigInteger(), nullable=True),
        sa.Column("sell_order_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("sell_price", sa.Numeric(), nullable=True),
        sa.Column("sell_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sell_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("fee_usdt", sa.Numeric(), nullable=True),
        sa.Column("net_profit_usdt", sa.Numeric(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("idx_lots_open", "lots", ["account_id", "symbol", "status"])
    op.create_index("idx_lots_strategy", "lots", ["account_id", "strategy_name", "status"])

    # positions
    op.create_table(
        "positions",
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("trading_accounts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("symbol", sa.String(), primary_key=True),
        sa.Column("qty", sa.Numeric(), nullable=False),
        sa.Column("cost_basis_usdt", sa.Numeric(), nullable=False),
        sa.Column("avg_entry", sa.Numeric(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # core_btc_history
    op.create_table(
        "core_btc_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("btc_qty", sa.Numeric(), nullable=False),
        sa.Column("cost_usdt", sa.Numeric(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_core_btc_history_account", "core_btc_history", ["account_id"])

    # price_snapshots
    op.create_table(
        "price_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("ts_ms", sa.BigInteger(), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_price_snapshots_symbol_ts", "price_snapshots", ["symbol", "ts_ms"], unique=True)

    # price_candles_5m
    op.create_table(
        "price_candles_5m",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("ts_ms", sa.BigInteger(), nullable=False),
        sa.Column("open", sa.Numeric(), nullable=False),
        sa.Column("high", sa.Numeric(), nullable=False),
        sa.Column("low", sa.Numeric(), nullable=False),
        sa.Column("close", sa.Numeric(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_price_candles_5m_symbol_ts", "price_candles_5m", ["symbol", "ts_ms"], unique=True)

    # is_admin() SECURITY DEFINER function (for Supabase RLS)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.is_admin()
        RETURNS BOOLEAN
        LANGUAGE sql
        SECURITY DEFINER
        STABLE
        AS $$
            SELECT EXISTS (
                SELECT 1 FROM public.user_profiles
                WHERE id = auth.uid() AND role = 'admin'
            );
        $$;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.is_admin()")
    op.drop_table("price_candles_5m")
    op.drop_table("price_snapshots")
    op.drop_table("core_btc_history")
    op.drop_table("positions")
    op.drop_table("lots")
    op.drop_table("fills")
    op.drop_table("orders")
    op.drop_table("strategy_state")
    op.drop_table("strategy_configs")
    op.drop_table("trading_accounts")
    op.drop_table("user_profiles")
