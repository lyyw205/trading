import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class StrategyConfig(TimestampMixin, Base):
    __tablename__ = "strategy_configs"
    __table_args__ = (
        UniqueConstraint("account_id", "strategy_name", name="uq_strategy_per_account"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=False
    )
    strategy_name: Mapped[str] = mapped_column(String, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    params: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")

    account = relationship("TradingAccount", back_populates="strategy_configs")

    def __repr__(self) -> str:
        return f"<StrategyConfig id={self.id} strategy={self.strategy_name!r} enabled={self.is_enabled}>"
