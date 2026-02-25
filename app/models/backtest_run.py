import uuid
from datetime import datetime
from sqlalchemy import String, Numeric, BigInteger, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    strategies: Mapped[dict] = mapped_column(JSONB, nullable=False)
    strategy_params: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    initial_usdt: Mapped[float] = mapped_column(Numeric, nullable=False)
    start_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="PENDING")
    result_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    trade_log: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    equity_curve: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
