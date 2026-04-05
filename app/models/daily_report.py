"""Daily operational report model."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DailyReport(Base):
    __tablename__ = "daily_reports"
    __table_args__ = (UniqueConstraint("report_date", name="uq_daily_report_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    health_score: Mapped[float] = mapped_column(Numeric(5, 2, asdecimal=False), nullable=False)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    telegram_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discord_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
