"""
Candle Store — write/read/aggregate service for multi-timeframe candle data.

All functions receive an AsyncSession and are designed to be called within
an existing transaction context OR with a fresh session.
"""
from __future__ import annotations

import logging
from sqlalchemy import select, func, delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.price_candle import PriceCandle1m, PriceCandle5m, PriceCandle1h, PriceCandle1d

logger = logging.getLogger(__name__)

# Table mapping for interval-based queries
_TABLE_MAP = {
    "1m": PriceCandle1m,
    "5m": PriceCandle5m,
    "1h": PriceCandle1h,
    "1d": PriceCandle1d,
}

BUCKET_MS = {
    "5m": 5 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}


async def store_closed_candle_1m(
    symbol: str,
    ts_ms: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 0.0,
    quote_volume: float = 0.0,
    trade_count: int = 0,
    *,
    session: AsyncSession,
) -> None:
    """Insert a completed 1m candle. Idempotent (ON CONFLICT DO NOTHING)."""
    stmt = (
        pg_insert(PriceCandle1m)
        .values(
            symbol=symbol,
            ts_ms=ts_ms,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            quote_volume=quote_volume,
            trade_count=trade_count,
        )
        .on_conflict_do_nothing(index_elements=["symbol", "ts_ms"])
    )
    await session.execute(stmt)


async def store_candles_batch_1m(
    candles: list[dict],
    *,
    session: AsyncSession,
) -> int:
    """Bulk insert 1m candles. Each dict must have: symbol, ts_ms, open, high, low, close, volume, quote_volume, trade_count.
    Returns the number of candles inserted (skips duplicates)."""
    if not candles:
        return 0
    stmt = pg_insert(PriceCandle1m).values(candles).on_conflict_do_nothing(index_elements=["symbol", "ts_ms"])
    result = await session.execute(stmt)
    return result.rowcount


async def get_candles(
    symbol: str,
    from_ts_ms: int,
    to_ts_ms: int,
    interval: str = "5m",
    *,
    session: AsyncSession,
) -> list:
    """Return candles for symbol in [from_ts_ms, to_ts_ms] ordered by ts_ms.
    Interval: '1m', '5m', '1h', '1d'. Defaults to '5m' for backward compat."""
    model = _TABLE_MAP.get(interval)
    if model is None:
        raise ValueError(f"Unknown interval: {interval}")
    stmt = (
        select(model)
        .where(
            model.symbol == symbol,
            model.ts_ms >= from_ts_ms,
            model.ts_ms <= to_ts_ms,
        )
        .order_by(model.ts_ms)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def aggregate_candles(
    symbol: str,
    source_interval: str,
    target_interval: str,
    cutoff_ts_ms: int,
    *,
    session: AsyncSession,
) -> int:
    """Aggregate candles from source to target interval for data older than cutoff_ts_ms.

    Uses SQL GROUP BY with floor division for bucketing.
    INSERT ON CONFLICT DO NOTHING for idempotency.
    Returns number of aggregated candles inserted.

    Source and target must be adjacent: 1m->5m, 5m->1h, 1h->1d.
    """
    source_model = _TABLE_MAP.get(source_interval)
    target_model = _TABLE_MAP.get(target_interval)
    bucket_ms = BUCKET_MS.get(target_interval)

    if not source_model or not target_model or not bucket_ms:
        raise ValueError(f"Invalid interval pair: {source_interval} -> {target_interval}")

    # Use raw SQL for proper OHLCV aggregation with first/last open/close
    raw_sql = text(f"""
        INSERT INTO {target_model.__tablename__} (symbol, ts_ms, "open", high, low, "close", volume, quote_volume, trade_count)
        SELECT
            symbol,
            (floor(ts_ms / :bucket_ms) * :bucket_ms)::bigint AS bucket_ts,
            (array_agg("open" ORDER BY ts_ms ASC))[1] AS "open",
            max(high) AS high,
            min(low) AS low,
            (array_agg("close" ORDER BY ts_ms DESC))[1] AS "close",
            sum(volume) AS volume,
            sum(quote_volume) AS quote_volume,
            sum(trade_count)::integer AS trade_count
        FROM {source_model.__tablename__}
        WHERE symbol = :symbol AND ts_ms < :cutoff
        GROUP BY symbol, bucket_ts
        ON CONFLICT (symbol, ts_ms) DO NOTHING
    """)

    result = await session.execute(raw_sql, {
        "bucket_ms": bucket_ms,
        "symbol": symbol,
        "cutoff": cutoff_ts_ms,
    })
    return result.rowcount


async def delete_old_candles(
    symbol: str,
    interval: str,
    before_ts_ms: int,
    *,
    session: AsyncSession,
) -> int:
    """Delete candles older than before_ts_ms. Returns count deleted."""
    model = _TABLE_MAP.get(interval)
    if not model:
        raise ValueError(f"Unknown interval: {interval}")
    stmt = delete(model).where(
        model.symbol == symbol,
        model.ts_ms < before_ts_ms,
    )
    result = await session.execute(stmt)
    return result.rowcount
