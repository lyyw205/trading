import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

LOT_STATUSES = ("OPEN", "CLOSED")


class Lot(Base):
    __tablename__ = "lots"
    __table_args__ = (
        Index("idx_lots_open", "account_id", "symbol", "status"),
        Index("idx_lots_strategy", "account_id", "strategy_name", "status"),
        Index("idx_lots_combo", "account_id", "combo_id", "status"),
        Index("idx_lots_buy_time", "buy_time"),
        CheckConstraint(f"status IN {LOT_STATUSES!r}", name="chk_lot_status"),
    )

    lot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    strategy_name: Mapped[str] = mapped_column(String, nullable=False, server_default="lot_stacking")
    buy_order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    buy_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    buy_qty: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    buy_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    buy_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String, server_default="OPEN")
    sell_order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sell_order_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sell_price: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    sell_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sell_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fee_usdt: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    net_profit_usdt: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    combo_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trading_combos.id"), nullable=True
    )
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, server_default="{}")

    account = relationship("TradingAccount", back_populates="lots")

    def __repr__(self) -> str:
        return f"<Lot lot_id={self.lot_id} account={self.account_id} status={self.status!r} buy_price={self.buy_price}>"
