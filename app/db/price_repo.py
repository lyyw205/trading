from __future__ import annotations

from sqlalchemy import select, func, literal
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.price_candle import PriceCandle5m, PriceCandle1m, PriceCandle1h, PriceCandle1d

# Interval -> Model mapping
_TABLE_MAP = {
    "1m": PriceCandle1m,
    "5m": PriceCandle5m,
    "1h": PriceCandle1h,
    "1d": PriceCandle1d,
}


async def upsert_candle_5m(
    symbol: str,
    ts_ms: int,
    price: float,
    session: AsyncSession,
) -> None:
    """Upsert a 5-minute candle bucket (DEPRECATED — kept for backward compat).

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
    interval: str = "5m",
) -> list:
    """Return candles for symbol in [from_ts_ms, to_ts_ms] ordered by ts_ms.

    Supports intervals: '1m', '5m', '1h', '1d'. Default '5m' for backward compat.
    """
    model = _TABLE_MAP.get(interval, PriceCandle5m)
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
