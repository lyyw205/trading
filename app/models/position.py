import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UpdatedAtMixin


class Position(UpdatedAtMixin, Base):
    __tablename__ = "positions"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    qty: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    cost_basis_usdt: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    avg_entry: Mapped[Decimal] = mapped_column(Numeric, nullable=False)

    account = relationship("TradingAccount", back_populates="positions")

    def __repr__(self) -> str:
        return f"<Position account={self.account_id} symbol={self.symbol!r} qty={self.qty}>"
