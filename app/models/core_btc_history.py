import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin

CORE_BTC_SOURCES = ("INIT", "MANUAL_APPROVE", "AUTO_RESERVE", "ADJUSTMENT")


class CoreBtcHistory(CreatedAtMixin, Base):
    __tablename__ = "core_btc_history"
    __table_args__ = (
        Index("idx_core_btc_history_account", "account_id"),
        CheckConstraint(f"source IN {CORE_BTC_SOURCES!r}", name="chk_core_btc_source"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    btc_qty: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    cost_usdt: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
