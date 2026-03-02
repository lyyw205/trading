from sqlalchemy import Index

from app.models.base import Base, PriceCandleMixin, UpdatedAtMixin


class PriceCandle5m(PriceCandleMixin, UpdatedAtMixin, Base):
    """5-minute candles — upserted from real-time price feed."""

    __tablename__ = "price_candles_5m"
    __table_args__ = (
        Index("idx_price_candles_5m_symbol_ts", "symbol", "ts_ms", unique=True),
    )


class PriceCandle1m(PriceCandleMixin, Base):
    """1-minute candles — write-once from kline WebSocket."""

    __tablename__ = "price_candles_1m"
    __table_args__ = (
        Index("idx_price_candles_1m_symbol_ts", "symbol", "ts_ms", unique=True),
    )


class PriceCandle1h(PriceCandleMixin, Base):
    """1-hour candles — aggregated from 5m candles."""

    __tablename__ = "price_candles_1h"
    __table_args__ = (
        Index("idx_price_candles_1h_symbol_ts", "symbol", "ts_ms", unique=True),
    )


class PriceCandle1d(PriceCandleMixin, Base):
    """1-day candles — aggregated from 1h candles."""

    __tablename__ = "price_candles_1d"
    __table_args__ = (
        Index("idx_price_candles_1d_symbol_ts", "symbol", "ts_ms", unique=True),
    )
