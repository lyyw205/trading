import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, Integer, Numeric, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class TradingAccount(Base):
    __tablename__ = "trading_accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    exchange: Mapped[str] = mapped_column(String, nullable=False, server_default="binance")
    symbol: Mapped[str] = mapped_column(String, nullable=False, server_default="ETHUSDT")
    base_asset: Mapped[str] = mapped_column(String, nullable=False, server_default="ETH")
    quote_asset: Mapped[str] = mapped_column(String, nullable=False, server_default="USDT")
    api_key_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    api_secret_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    encryption_key_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    circuit_breaker_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    circuit_breaker_disabled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(nullable=True)
    pending_earnings_usdt: Mapped[float] = mapped_column(
        Numeric, nullable=False, server_default="0"
    )
    loop_interval_sec: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")
    order_cooldown_sec: Mapped[int] = mapped_column(Integer, nullable=False, server_default="7")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    owner = relationship("UserProfile", back_populates="accounts")
    strategy_configs = relationship("StrategyConfig", back_populates="account", cascade="all, delete-orphan")
    strategy_states = relationship("StrategyState", back_populates="account", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="account", cascade="all, delete-orphan")
    fills = relationship("Fill", back_populates="account", cascade="all, delete-orphan")
    lots = relationship("Lot", back_populates="account", cascade="all, delete-orphan")
    positions = relationship("Position", back_populates="account", cascade="all, delete-orphan")
    trading_combos = relationship("TradingCombo", back_populates="account", cascade="all, delete-orphan")
