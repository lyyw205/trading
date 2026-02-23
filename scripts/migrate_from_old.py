#!/usr/bin/env python3
"""
migrate_from_old.py
-------------------
Migrate data from btc-staking-bot (legacy) PostgreSQL database to
the crypto-multi-trader database.

Usage:
    python scripts/migrate_from_old.py \
        --legacy-db-url "postgresql://..." \
        --new-db-url "postgresql+asyncpg://..." \
        --api-key "BINANCE_KEY" \
        --api-secret "BINANCE_SECRET" \
        --account-label "My BTC Account" \
        [--dry-run]

Environment variables (override CLI flags):
    LEGACY_DB_URL   - legacy database URL
    NEW_DB_URL      - new database URL (asyncpg)
    ENCRYPTION_KEYS - comma-separated Fernet keys for new DB encryption
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except Exception:
        return default


def _int(val, default: int | None = None):
    try:
        return int(val) if val is not None else default
    except Exception:
        return default


def _str(val, default: str = "") -> str:
    return str(val) if val is not None else default


# ---------------------------------------------------------------------------
# Legacy DB reads (asyncpg)
# ---------------------------------------------------------------------------

async def fetch_settings(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch("SELECT key, value FROM btc_settings")
    return {r["key"]: r["value"] for r in rows}


async def fetch_lots(conn: asyncpg.Connection, table: str) -> list[dict]:
    rows = await conn.fetch(f"SELECT * FROM {table} ORDER BY lot_id ASC")
    return [dict(r) for r in rows]


async def fetch_orders(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch("SELECT * FROM btc_orders ORDER BY order_id ASC")
    return [dict(r) for r in rows]


async def fetch_fills(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch("SELECT * FROM btc_fills ORDER BY trade_id ASC")
    return [dict(r) for r in rows]


async def fetch_position(conn: asyncpg.Connection, symbol: str) -> dict | None:
    row = await conn.fetchrow(
        "SELECT * FROM btc_position WHERE symbol = $1", symbol
    )
    return dict(row) if row else None


async def fetch_core_history(conn: asyncpg.Connection) -> list[dict]:
    try:
        rows = await conn.fetch(
            "SELECT * FROM btc_core_history ORDER BY id ASC"
        )
        return [dict(r) for r in rows]
    except asyncpg.UndefinedTableError:
        logger.warning("btc_core_history table not found – skipping")
        return []


async def fetch_price_snapshots(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch(
        "SELECT * FROM btc_price_snapshots ORDER BY id ASC"
    )
    return [dict(r) for r in rows]


async def fetch_price_candles(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch(
        "SELECT * FROM btc_price_candles_5m ORDER BY id ASC"
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# New DB writes (SQLAlchemy async)
# ---------------------------------------------------------------------------

async def create_account(
    session: AsyncSession,
    owner_id: uuid.UUID,
    label: str,
    symbol: str,
    api_key_enc: str,
    api_secret_enc: str,
) -> uuid.UUID:
    account_id = uuid.uuid4()
    base_asset = symbol.replace("USDT", "")
    await session.execute(
        text(
            """
            INSERT INTO trading_accounts
              (id, owner_id, name, exchange, symbol, base_asset, quote_asset,
               api_key_encrypted, api_secret_encrypted, encryption_key_version,
               is_active, created_at, updated_at)
            VALUES
              (:id, :owner_id, :name, 'binance', :symbol, :base_asset, 'USDT',
               :api_key, :api_secret, 1,
               true, now(), now())
            ON CONFLICT DO NOTHING
            """
        ),
        {
            "id": account_id,
            "owner_id": owner_id,
            "name": label,
            "symbol": symbol,
            "base_asset": base_asset,
            "api_key": api_key_enc,
            "api_secret": api_secret_enc,
        },
    )
    return account_id


async def upsert_strategy_state(
    session: AsyncSession,
    account_id: uuid.UUID,
    scope: str,
    key: str,
    value: str,
):
    await session.execute(
        text(
            """
            INSERT INTO strategy_state (account_id, scope, key, value)
            VALUES (:account_id, :scope, :key, :value)
            ON CONFLICT (account_id, scope, key)
            DO UPDATE SET value = EXCLUDED.value
            """
        ),
        {"account_id": account_id, "scope": scope, "key": key, "value": value},
    )


async def upsert_strategy_config(
    session: AsyncSession,
    account_id: uuid.UUID,
    strategy_name: str,
    params: dict,
):
    await session.execute(
        text(
            """
            INSERT INTO strategy_configs
              (id, account_id, strategy_name, is_enabled, params, created_at, updated_at)
            VALUES
              (:id, :account_id, :strategy_name, true, :params::jsonb, now(), now())
            ON CONFLICT (account_id, strategy_name)
            DO UPDATE SET params = EXCLUDED.params, updated_at = now()
            """
        ),
        {
            "id": uuid.uuid4(),
            "account_id": account_id,
            "strategy_name": strategy_name,
            "params": json.dumps(params),
        },
    )


async def insert_lot(
    session: AsyncSession,
    account_id: uuid.UUID,
    lot: dict,
    strategy_name: str,
):
    await session.execute(
        text(
            """
            INSERT INTO lots
              (lot_id, account_id, symbol, strategy_name,
               buy_order_id, buy_price, buy_qty, buy_time, buy_time_ms,
               status, sell_order_id, sell_order_time_ms,
               sell_price, sell_time, sell_time_ms,
               fee_usdt, net_profit_usdt)
            VALUES
              (:lot_id, :account_id, :symbol, :strategy_name,
               :buy_order_id, :buy_price, :buy_qty, :buy_time, :buy_time_ms,
               :status, :sell_order_id, :sell_order_time_ms,
               :sell_price, :sell_time, :sell_time_ms,
               :fee_usdt, :net_profit_usdt)
            ON CONFLICT (lot_id, account_id) DO NOTHING
            """
        ),
        {
            "lot_id": int(lot["lot_id"]),
            "account_id": account_id,
            "symbol": _str(lot.get("symbol"), "BTCUSDT"),
            "strategy_name": strategy_name,
            "buy_order_id": _int(lot.get("buy_order_id")),
            "buy_price": _float(lot.get("buy_price")),
            "buy_qty": _float(lot.get("buy_btc_qty")),
            "buy_time": lot.get("buy_time"),
            "buy_time_ms": _int(lot.get("buy_time_ms")),
            "status": _str(lot.get("status"), "OPEN"),
            "sell_order_id": _int(lot.get("sell_order_id")),
            "sell_order_time_ms": _int(lot.get("sell_order_time_ms")),
            "sell_price": _float(lot.get("sell_price")) if lot.get("sell_price") else None,
            "sell_time": lot.get("sell_time"),
            "sell_time_ms": _int(lot.get("sell_time_ms")),
            "fee_usdt": _float(lot.get("fee_usdt")) if lot.get("fee_usdt") else None,
            "net_profit_usdt": _float(lot.get("net_profit_usdt")) if lot.get("net_profit_usdt") else None,
        },
    )


async def insert_order(
    session: AsyncSession,
    account_id: uuid.UUID,
    order: dict,
):
    raw = dict(order)
    # asyncpg returns datetime objects; convert for JSON serialisation
    for k, v in raw.items():
        if isinstance(v, datetime):
            raw[k] = v.isoformat()

    await session.execute(
        text(
            """
            INSERT INTO orders
              (order_id, account_id, symbol, side, type, status,
               price, orig_qty, executed_qty, cum_quote_qty,
               client_order_id, update_time_ms, raw_json, updated_at)
            VALUES
              (:order_id, :account_id, :symbol, :side, :type, :status,
               :price, :orig_qty, :executed_qty, :cum_quote_qty,
               :client_order_id, :update_time_ms, :raw_json::jsonb, now())
            ON CONFLICT (order_id, account_id) DO NOTHING
            """
        ),
        {
            "order_id": int(order["order_id"]),
            "account_id": account_id,
            "symbol": _str(order.get("symbol")),
            "side": order.get("side"),
            "type": order.get("type"),
            "status": order.get("status"),
            "price": _float(order.get("price")) if order.get("price") else None,
            "orig_qty": _float(order.get("orig_qty")) if order.get("orig_qty") else None,
            "executed_qty": _float(order.get("executed_qty")) if order.get("executed_qty") else None,
            "cum_quote_qty": _float(order.get("cum_quote_qty")) if order.get("cum_quote_qty") else None,
            "client_order_id": order.get("client_order_id"),
            "update_time_ms": _int(order.get("update_time_ms")),
            "raw_json": json.dumps(raw),
        },
    )


async def insert_fill(
    session: AsyncSession,
    account_id: uuid.UUID,
    fill: dict,
):
    raw = dict(fill)
    for k, v in raw.items():
        if isinstance(v, datetime):
            raw[k] = v.isoformat()

    await session.execute(
        text(
            """
            INSERT INTO fills
              (trade_id, account_id, order_id, symbol, side,
               price, qty, quote_qty, commission, commission_asset,
               trade_time_ms, raw_json)
            VALUES
              (:trade_id, :account_id, :order_id, :symbol, :side,
               :price, :qty, :quote_qty, :commission, :commission_asset,
               :trade_time_ms, :raw_json::jsonb)
            ON CONFLICT (trade_id, account_id) DO NOTHING
            """
        ),
        {
            "trade_id": int(fill["trade_id"]),
            "account_id": account_id,
            "order_id": _int(fill.get("order_id")),
            "symbol": _str(fill.get("symbol")),
            "side": fill.get("side"),
            "price": _float(fill.get("price")) if fill.get("price") else None,
            "qty": _float(fill.get("qty")) if fill.get("qty") else None,
            "quote_qty": _float(fill.get("quote_qty")) if fill.get("quote_qty") else None,
            "commission": _float(fill.get("commission")) if fill.get("commission") else None,
            "commission_asset": fill.get("commission_asset"),
            "trade_time_ms": _int(fill.get("trade_time_ms")),
            "raw_json": json.dumps(raw),
        },
    )


async def insert_position(
    session: AsyncSession,
    account_id: uuid.UUID,
    pos: dict,
):
    await session.execute(
        text(
            """
            INSERT INTO positions
              (account_id, symbol, qty, cost_basis_usdt, avg_entry, updated_at)
            VALUES
              (:account_id, :symbol, :qty, :cost_basis_usdt, :avg_entry, now())
            ON CONFLICT (account_id, symbol)
            DO UPDATE SET
              qty = EXCLUDED.qty,
              cost_basis_usdt = EXCLUDED.cost_basis_usdt,
              avg_entry = EXCLUDED.avg_entry,
              updated_at = now()
            """
        ),
        {
            "account_id": account_id,
            "symbol": _str(pos.get("symbol")),
            "qty": _float(pos.get("btc_qty")),
            "cost_basis_usdt": _float(pos.get("cost_basis_usdt")),
            "avg_entry": _float(pos.get("avg_entry")),
        },
    )


async def insert_core_history(
    session: AsyncSession,
    account_id: uuid.UUID,
    row: dict,
    symbol: str,
):
    await session.execute(
        text(
            """
            INSERT INTO core_btc_history
              (account_id, symbol, btc_qty, cost_usdt, source, created_at)
            VALUES
              (:account_id, :symbol, :btc_qty, :cost_usdt, :source, :created_at)
            """
        ),
        {
            "account_id": account_id,
            "symbol": _str(row.get("symbol"), symbol),
            "btc_qty": _float(row.get("btc_qty")),
            "cost_usdt": _float(row.get("cost_usdt")),
            "source": _str(row.get("source"), "migration"),
            "created_at": row.get("created_at") or datetime.utcnow(),
        },
    )


async def insert_price_snapshot(
    session: AsyncSession,
    row: dict,
):
    await session.execute(
        text(
            """
            INSERT INTO price_snapshots (symbol, ts_ms, price)
            VALUES (:symbol, :ts_ms, :price)
            ON CONFLICT (symbol, ts_ms) DO NOTHING
            """
        ),
        {
            "symbol": _str(row.get("symbol")),
            "ts_ms": int(row["ts_ms"]),
            "price": _float(row.get("price")),
        },
    )


async def insert_price_candle(
    session: AsyncSession,
    row: dict,
):
    await session.execute(
        text(
            """
            INSERT INTO price_candles_5m (symbol, ts_ms, open, high, low, close)
            VALUES (:symbol, :ts_ms, :open, :high, :low, :close)
            ON CONFLICT (symbol, ts_ms) DO NOTHING
            """
        ),
        {
            "symbol": _str(row.get("symbol")),
            "ts_ms": int(row["ts_ms"]),
            "open": _float(row.get("open")),
            "high": _float(row.get("high")),
            "low": _float(row.get("low")),
            "close": _float(row.get("close")),
        },
    )


# ---------------------------------------------------------------------------
# Strategy state mapping
# ---------------------------------------------------------------------------

# Maps (legacy btc_settings key) -> (new scope, new key)
SETTINGS_MAP: list[tuple[str, str, str]] = [
    # lot_stacking scope
    ("base_price.BTCUSDT",               "lot_stacking", "base_price"),
    ("lot_recenter_ema.BTCUSDT",          "lot_stacking", "recenter_ema"),
    ("pending_buy_order_id",              "lot_stacking", "pending_order_id"),
    ("pending_buy_time_ms",               "lot_stacking", "pending_time_ms"),
    ("pending_buy_core_bucket_usdt",      "lot_stacking", "pending_core_bucket_usdt"),
    ("pending_buy_kind",                  "lot_stacking", "pending_kind"),
    ("pending_buy_trigger_price",         "lot_stacking", "pending_trigger_price"),
    ("core_bucket_usdt",                  "lot_stacking", "core_bucket_usdt"),
    ("core_btc_initial",                  "lot_stacking", "core_btc_initial"),
    ("usdt_ref_total",                    "lot_stacking", "usdt_ref_total"),
    # trend_buy scope
    ("trend_base_price",                  "trend_buy",    "base_price"),
    ("last_trend_buy_price",              "trend_buy",    "last_buy_price"),
    ("trend_pending_order_id",            "trend_buy",    "pending_order_id"),
    ("trend_pending_time_ms",             "trend_buy",    "pending_time_ms"),
    ("trend_pending_trend_bucket_usdt",   "trend_buy",    "pending_trend_bucket_usdt"),
    ("trend_pending_trigger_price",       "trend_buy",    "pending_trigger_price"),
    ("trend_core_bucket_usdt",            "trend_buy",    "core_bucket_usdt"),
    # shared scope
    ("reserve_btc_qty",                   "shared",       "reserve_qty"),
    ("reserve_cost_usdt",                 "shared",       "reserve_cost_usdt"),
]

# tune.lot_* -> strategy_configs[lot_stacking].params.*
# tune.trend_* -> strategy_configs[trend_buy].params.*
# The legacy key is stored as JSON: tune -> {"lot_buy_usdt": 100, ...}

def _extract_tune_params(settings: dict[str, str]) -> tuple[dict, dict]:
    """Return (lot_params, trend_params) from the 'tune' key in btc_settings."""
    raw = settings.get("tune", "{}")
    try:
        tune: dict = json.loads(raw)
    except Exception:
        tune = {}

    lot_params: dict = {}
    trend_params: dict = {}

    for k, v in tune.items():
        if k.startswith("lot_"):
            lot_params[k] = v
        elif k.startswith("trend_"):
            trend_params[k] = v
        # others are ignored

    return lot_params, trend_params


# ---------------------------------------------------------------------------
# Main migration coroutine
# ---------------------------------------------------------------------------

async def migrate(
    legacy_db_url: str,
    new_db_url: str,
    api_key: str,
    api_secret: str,
    account_label: str,
    symbol: str,
    dry_run: bool,
):
    # ------------------------------------------------------------------
    # Connect to legacy DB
    # ------------------------------------------------------------------
    # asyncpg uses the plain postgresql:// scheme
    legacy_url = legacy_db_url.replace("postgresql+asyncpg://", "postgresql://")
    logger.info("Connecting to legacy DB …")
    legacy_conn: asyncpg.Connection = await asyncpg.connect(legacy_url)

    # ------------------------------------------------------------------
    # Read all legacy data
    # ------------------------------------------------------------------
    logger.info("Reading legacy settings …")
    settings = await fetch_settings(legacy_conn)

    logger.info("Reading legacy lots …")
    lots = await fetch_lots(legacy_conn, "btc_lots")
    trend_lots = await fetch_lots(legacy_conn, "btc_trend_lots")

    logger.info("Reading legacy orders …")
    orders = await fetch_orders(legacy_conn)

    logger.info("Reading legacy fills …")
    fills = await fetch_fills(legacy_conn)

    logger.info("Reading legacy position …")
    position = await fetch_position(legacy_conn, symbol)

    logger.info("Reading legacy core_btc_history …")
    core_history = await fetch_core_history(legacy_conn)

    logger.info("Reading legacy price_snapshots …")
    snapshots = await fetch_price_snapshots(legacy_conn)

    logger.info("Reading legacy price_candles_5m …")
    candles = await fetch_price_candles(legacy_conn)

    await legacy_conn.close()

    logger.info(
        "Loaded: %d settings, %d lots, %d trend_lots, %d orders, %d fills, "
        "%d snapshots, %d candles, %d core_history",
        len(settings), len(lots), len(trend_lots), len(orders), len(fills),
        len(snapshots), len(candles), len(core_history),
    )

    if dry_run:
        logger.info("DRY RUN – no writes performed. Exiting.")
        return

    # ------------------------------------------------------------------
    # Connect to new DB
    # ------------------------------------------------------------------
    engine = create_async_engine(new_db_url, echo=False)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Encryption for API keys
    enc_keys_env = os.getenv("ENCRYPTION_KEYS", "")
    if not enc_keys_env:
        logger.warning(
            "ENCRYPTION_KEYS not set – storing API key/secret as-is (unsafe for production)"
        )
        api_key_enc = api_key
        api_secret_enc = api_secret
    else:
        from app.utils.encryption import EncryptionManager
        enc = EncryptionManager(enc_keys_env.split(","))
        api_key_enc = enc.encrypt(api_key)
        api_secret_enc = enc.encrypt(api_secret)

    async with SessionLocal() as session:
        # ------------------------------------------------------------------
        # Create account (use a deterministic owner_id placeholder)
        # ------------------------------------------------------------------
        owner_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        logger.info("Creating trading account '%s' …", account_label)
        account_id = await create_account(
            session, owner_id, account_label, symbol, api_key_enc, api_secret_enc
        )

        # ------------------------------------------------------------------
        # Strategy state
        # ------------------------------------------------------------------
        logger.info("Migrating strategy state …")
        for legacy_key, scope, new_key in SETTINGS_MAP:
            val = settings.get(legacy_key)
            if val is not None:
                await upsert_strategy_state(session, account_id, scope, new_key, val)

        # ------------------------------------------------------------------
        # Strategy configs (tune params)
        # ------------------------------------------------------------------
        lot_params, trend_params = _extract_tune_params(settings)
        if lot_params:
            logger.info("Migrating lot_stacking config params: %s", list(lot_params))
            await upsert_strategy_config(session, account_id, "lot_stacking", lot_params)
        if trend_params:
            logger.info("Migrating trend_buy config params: %s", list(trend_params))
            await upsert_strategy_config(session, account_id, "trend_buy", trend_params)

        # ------------------------------------------------------------------
        # Lots
        # ------------------------------------------------------------------
        logger.info("Migrating %d lots (lot_stacking) …", len(lots))
        for lot in lots:
            await insert_lot(session, account_id, lot, "lot_stacking")

        logger.info("Migrating %d trend_lots (trend_buy) …", len(trend_lots))
        for lot in trend_lots:
            await insert_lot(session, account_id, lot, "trend_buy")

        # ------------------------------------------------------------------
        # Orders
        # ------------------------------------------------------------------
        logger.info("Migrating %d orders …", len(orders))
        for order in orders:
            await insert_order(session, account_id, order)

        # ------------------------------------------------------------------
        # Fills
        # ------------------------------------------------------------------
        logger.info("Migrating %d fills …", len(fills))
        for fill in fills:
            await insert_fill(session, account_id, fill)

        # ------------------------------------------------------------------
        # Position
        # ------------------------------------------------------------------
        if position:
            logger.info("Migrating position for %s …", symbol)
            await insert_position(session, account_id, position)

        # ------------------------------------------------------------------
        # Core BTC history
        # ------------------------------------------------------------------
        logger.info("Migrating %d core_btc_history rows …", len(core_history))
        for row in core_history:
            await insert_core_history(session, account_id, row, symbol)

        # ------------------------------------------------------------------
        # Price snapshots
        # ------------------------------------------------------------------
        logger.info("Migrating %d price_snapshots …", len(snapshots))
        for row in snapshots:
            await insert_price_snapshot(session, row)

        # ------------------------------------------------------------------
        # Price candles
        # ------------------------------------------------------------------
        logger.info("Migrating %d price_candles_5m …", len(candles))
        for row in candles:
            await insert_price_candle(session, row)

        await session.commit()

    await engine.dispose()

    logger.info(
        "Migration complete. account_id=%s  label='%s'",
        account_id,
        account_label,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate btc-staking-bot data to crypto-multi-trader"
    )
    parser.add_argument(
        "--legacy-db-url",
        default=os.getenv("LEGACY_DB_URL", ""),
        help="Legacy PostgreSQL URL (postgresql://...)",
    )
    parser.add_argument(
        "--new-db-url",
        default=os.getenv("NEW_DB_URL", ""),
        help="New PostgreSQL URL (postgresql+asyncpg://...)",
    )
    parser.add_argument("--api-key", default="", help="Binance API key")
    parser.add_argument("--api-secret", default="", help="Binance API secret")
    parser.add_argument(
        "--account-label", default="Migrated Account", help="Label for the new account"
    )
    parser.add_argument(
        "--symbol", default="BTCUSDT", help="Trading symbol (default: BTCUSDT)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read legacy data and report counts without writing to new DB",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if not args.legacy_db_url:
        logger.error("--legacy-db-url is required (or set LEGACY_DB_URL)")
        sys.exit(1)
    if not args.dry_run and not args.new_db_url:
        logger.error("--new-db-url is required (or set NEW_DB_URL)")
        sys.exit(1)

    asyncio.run(
        migrate(
            legacy_db_url=args.legacy_db_url,
            new_db_url=args.new_db_url,
            api_key=args.api_key,
            api_secret=args.api_secret,
            account_label=args.account_label,
            symbol=args.symbol,
            dry_run=args.dry_run,
        )
    )
