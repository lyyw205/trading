import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CoreBtcHistory(Base):
    __tablename__ = "core_btc_history"
    __table_args__ = (
        Index("idx_core_btc_history_account", "account_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    btc_qty: Mapped[float] = mapped_column(Numeric, nullable=False)
    cost_usdt: Mapped[float] = mapped_column(Numeric, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
