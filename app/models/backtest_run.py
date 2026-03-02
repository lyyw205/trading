import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin

BACKTEST_STATUSES = ("PENDING", "RUNNING", "COMPLETED", "FAILED")


class BacktestRun(CreatedAtMixin, Base):
    __tablename__ = "backtest_runs"
    __table_args__ = (
        Index("idx_backtest_runs_user", "user_id", "created_at"),
        CheckConstraint(f"status IN {BACKTEST_STATUSES!r}", name="chk_backtest_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    combos: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    strategies: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    strategy_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    initial_usdt: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    start_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="PENDING")
    result_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    trade_log: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    equity_curve: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
