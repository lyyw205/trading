from datetime import datetime
from sqlalchemy import BigInteger, String, Numeric, Integer, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class PriceCandle5m(Base):
    __tablename__ = "price_candles_5m"
    __table_args__ = (
        Index("idx_price_candles_5m_symbol_ts", "symbol", "ts_ms", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    open: Mapped[float] = mapped_column(Numeric, nullable=False)
    high: Mapped[float] = mapped_column(Numeric, nullable=False)
    low: Mapped[float] = mapped_column(Numeric, nullable=False)
    close: Mapped[float] = mapped_column(Numeric, nullable=False)
    volume: Mapped[float] = mapped_column(Numeric, nullable=False, server_default="0")
    quote_volume: Mapped[float] = mapped_column(Numeric, nullable=False, server_default="0")
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class PriceCandle1m(Base):
    __tablename__ = "price_candles_1m"
    __table_args__ = (
        Index("idx_price_candles_1m_symbol_ts", "symbol", "ts_ms", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    open: Mapped[float] = mapped_column(Numeric, nullable=False)
    high: Mapped[float] = mapped_column(Numeric, nullable=False)
    low: Mapped[float] = mapped_column(Numeric, nullable=False)
    close: Mapped[float] = mapped_column(Numeric, nullable=False)
    volume: Mapped[float] = mapped_column(Numeric, nullable=False, server_default="0")
    quote_volume: Mapped[float] = mapped_column(Numeric, nullable=False, server_default="0")
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PriceCandle1h(Base):
    __tablename__ = "price_candles_1h"
    __table_args__ = (
        Index("idx_price_candles_1h_symbol_ts", "symbol", "ts_ms", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    open: Mapped[float] = mapped_column(Numeric, nullable=False)
    high: Mapped[float] = mapped_column(Numeric, nullable=False)
    low: Mapped[float] = mapped_column(Numeric, nullable=False)
    close: Mapped[float] = mapped_column(Numeric, nullable=False)
    volume: Mapped[float] = mapped_column(Numeric, nullable=False, server_default="0")
    quote_volume: Mapped[float] = mapped_column(Numeric, nullable=False, server_default="0")
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PriceCandle1d(Base):
    __tablename__ = "price_candles_1d"
    __table_args__ = (
        Index("idx_price_candles_1d_symbol_ts", "symbol", "ts_ms", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    open: Mapped[float] = mapped_column(Numeric, nullable=False)
    high: Mapped[float] = mapped_column(Numeric, nullable=False)
    low: Mapped[float] = mapped_column(Numeric, nullable=False)
    close: Mapped[float] = mapped_column(Numeric, nullable=False)
    volume: Mapped[float] = mapped_column(Numeric, nullable=False, server_default="0")
    quote_volume: Mapped[float] = mapped_column(Numeric, nullable=False, server_default="0")
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
