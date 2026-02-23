from __future__ import annotations

from sqlalchemy import select, func, literal
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.price_snapshot import PriceSnapshot
from app.models.price_candle import PriceCandle5m


async def insert_snapshot(
    symbol: str,
    ts_ms: int,
    price: float,
    session: AsyncSession,
) -> None:
    """Insert a price snapshot; silently ignore duplicate (symbol, ts_ms)."""
    stmt = (
        pg_insert(PriceSnapshot)
        .values(symbol=symbol, ts_ms=ts_ms, price=price)
        .on_conflict_do_nothing(index_elements=["symbol", "ts_ms"])
    )
    await session.execute(stmt)


async def upsert_candle_5m(
    symbol: str,
    ts_ms: int,
    price: float,
    session: AsyncSession,
) -> None:
    """Upsert a 5-minute candle bucket.

    On insert: open = high = low = close = price.
    On conflict: update high = max(high, price), low = min(low, price), close = price.
    """
    stmt = (
        pg_insert(PriceCandle5m)
        .values(
            symbol=symbol,
            ts_ms=ts_ms,
            open=price,
            high=price,
            low=price,
            close=price,
        )
        .on_conflict_do_update(
            index_elements=["symbol", "ts_ms"],
            set_=dict(
                high=func.greatest(PriceCandle5m.high, literal(price)),
                low=func.least(PriceCandle5m.low, literal(price)),
                close=price,
            ),
        )
    )
    await session.execute(stmt)


async def get_candles(
    symbol: str,
    from_ts_ms: int,
    to_ts_ms: int,
    session: AsyncSession,
) -> list[PriceCandle5m]:
    """Return candles for symbol in [from_ts_ms, to_ts_ms] ordered by ts_ms."""
    stmt = (
        select(PriceCandle5m)
        .where(
            PriceCandle5m.symbol == symbol,
            PriceCandle5m.ts_ms >= from_ts_ms,
            PriceCandle5m.ts_ms <= to_ts_ms,
        )
        .order_by(PriceCandle5m.ts_ms)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_snapshots(
    symbol: str,
    from_ts_ms: int,
    to_ts_ms: int,
    session: AsyncSession,
) -> list[PriceSnapshot]:
    """Return snapshots for symbol in [from_ts_ms, to_ts_ms] ordered by ts_ms."""
    stmt = (
        select(PriceSnapshot)
        .where(
            PriceSnapshot.symbol == symbol,
            PriceSnapshot.ts_ms >= from_ts_ms,
            PriceSnapshot.ts_ms <= to_ts_ms,
        )
        .order_by(PriceSnapshot.ts_ms)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
