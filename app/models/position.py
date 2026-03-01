import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Position(Base):
    __tablename__ = "positions"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    qty: Mapped[float] = mapped_column(Numeric, nullable=False)
    cost_basis_usdt: Mapped[float] = mapped_column(Numeric, nullable=False)
    avg_entry: Mapped[float] = mapped_column(Numeric, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    account = relationship("TradingAccount", back_populates="positions")
