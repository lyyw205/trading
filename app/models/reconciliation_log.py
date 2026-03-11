"""ORM model for reconciliation_logs table (migration 017)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ReconciliationLog(Base):
    __tablename__ = "reconciliation_logs"
    __table_args__ = (Index("idx_recon_account_checked", "account_id", text("checked_at DESC")),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trading_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    position_diffs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    balance_diff: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fill_gaps: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    auto_resolved: Mapped[bool] = mapped_column(Boolean, server_default="false")
