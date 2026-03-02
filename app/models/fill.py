import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, ForeignKey, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Fill(Base):
    __tablename__ = "fills"
    __table_args__ = (
        Index("idx_fills_account_time", "account_id", "trade_time_ms"),
    )

    trade_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str | None] = mapped_column(String, nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    qty: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    quote_qty: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    commission: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    commission_asset: Mapped[str | None] = mapped_column(String, nullable=True)
    trade_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    inserted_at: Mapped[datetime] = mapped_column(server_default=func.now())

    account = relationship("TradingAccount", back_populates="fills")

    def __repr__(self) -> str:
        return f"<Fill trade_id={self.trade_id} symbol={self.symbol!r} side={self.side!r} price={self.price}>"
