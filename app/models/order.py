import uuid

from sqlalchemy import BigInteger, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UpdatedAtMixin


class Order(UpdatedAtMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("idx_orders_status", "account_id", "status"),
        Index("idx_orders_update_time", "update_time_ms"),
        Index("idx_orders_symbol", "symbol"),
    )

    order_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    orig_qty: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    executed_qty: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    cum_quote_qty: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    client_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    update_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    account = relationship("TradingAccount", back_populates="orders")

    def __repr__(self) -> str:
        return f"<Order order_id={self.order_id} symbol={self.symbol!r} side={self.side!r} status={self.status!r}>"
