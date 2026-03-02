import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class TradingCombo(TimestampMixin, Base):
    __tablename__ = "trading_combos"
    __table_args__ = (
        CheckConstraint("reference_combo_id != id", name="chk_no_self_reference"),
        Index("idx_combos_account", "account_id", "is_enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    symbols: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    buy_logic_name: Mapped[str] = mapped_column(String, nullable=False)
    buy_params: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    sell_logic_name: Mapped[str] = mapped_column(String, nullable=False)
    sell_params: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    reference_combo_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("trading_combos.id", ondelete="SET NULL"), nullable=True
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    account = relationship("TradingAccount", back_populates="trading_combos")

    def __repr__(self) -> str:
        return f"<TradingCombo id={self.id} name={self.name!r} enabled={self.is_enabled}>"
