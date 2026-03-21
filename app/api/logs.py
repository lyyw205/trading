"""
In-memory log ring buffer API endpoint.
Admin-only access to recent structured logs.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_trading_session
from app.dependencies import limiter, require_admin
from app.utils.logging import log_buffer

router = APIRouter(prefix="/api/logs", tags=["logs"])
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# User-facing log filter
# ---------------------------------------------------------------------------
# Patterns that regular users should see on their account dashboard.
# Everything else is internal/infra noise hidden from users.

_USER_VISIBLE_RE: re.Pattern[str] = re.compile(
    "|".join(
        (
            # Buy/sell fills (most important)
            r"LOT buy filled",
            r"TREND buy filled",
            r"INIT buy filled",
            r"lot \d+ TP filled",
            # Order placement
            r"placed LOT buy order",
            r"placed TREND buy order",
            r"placed TP sell order",
            # Buy pause state changes
            r"Buy pause",
            # Circuit breaker (trading halted)
            r"Circuit breaker triggered",
            r"Circuit breaker already tripped",
            r"Auto-recovering CB",
            # Balance/sizing warnings
            r"buy_usdt .+ below min_trade_usdt",
            r"Insufficient .+ balance",
            r"notional .+ below minimum",
            # Trading loop lifecycle (admin-only, 서버 재시작마다 발생)
            # r"트레이딩 루프가 정상 시작",
            # Scanning activity (periodic heartbeat)
            r"스캔 중:",
            # Sell-only monitoring heartbeat (PAUSED state)
            r"매도 감시 실행완료",
            # Sell order status changes (user cares if cancelled/expired)
            r"sell order \d+ for lot \d+ (CANCELED|EXPIRED)",
            # Place order failures (user's money is affected)
            r"place (LOT|TREND|TP) (buy|sell).*failed",
        )
    )
)


def _is_user_visible(entry: dict) -> bool:
    """Check if a log entry should be visible to regular users."""
    msg = entry.get("msg", "")
    return bool(_USER_VISIBLE_RE.search(msg))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/all")
@limiter.limit("60/minute")
async def get_all_logs(
    request: Request,
    _admin: dict = Depends(require_admin),
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    level: Annotated[str | None, Query(pattern="^(INFO|WARNING|ERROR|CRITICAL)$")] = None,
    account_id: Annotated[uuid.UUID | None, Query()] = None,
    module: Annotated[str | None, Query(max_length=100, pattern=r"^[a-zA-Z0-9_.\-]+$")] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> list[dict]:
    """
    Returns all recent in-memory log entries with optional filters.
    """
    results = log_buffer.get_logs(account_id=str(account_id) if account_id else None, level=level, limit=limit)
    if module:
        results = [e for e in results if e.get("module") == module]
    if search:
        search_lower = search.lower()
        results = [e for e in results if search_lower in (e.get("msg") or "").lower()]
    return results


# ---------------------------------------------------------------------------
# Persistent log endpoints (DB-backed)
# ---------------------------------------------------------------------------
# NOTE: These must be declared BEFORE /{account_id} to avoid route shadowing.


@router.get("/persistent")
@limiter.limit("60/minute")
async def get_persistent_logs(
    request: Request,
    _admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    level: Annotated[str | None, Query(pattern="^(ERROR|CRITICAL)$")] = None,
    account_id: Annotated[uuid.UUID | None, Query()] = None,
    from_date: Annotated[datetime | None, Query()] = None,
    to_date: Annotated[datetime | None, Query()] = None,
) -> list[dict]:
    """Query persistent ERROR/CRITICAL logs from database."""
    from sqlalchemy import select as sa_select

    from app.models.persistent_log import PersistentLog

    stmt = sa_select(PersistentLog).order_by(PersistentLog.logged_at.desc())

    if level:
        stmt = stmt.where(PersistentLog.level == level)
    if account_id:
        stmt = stmt.where(PersistentLog.account_id == account_id)
    if from_date:
        stmt = stmt.where(PersistentLog.logged_at >= from_date)
    if to_date:
        stmt = stmt.where(PersistentLog.logged_at <= to_date)

    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    logs = result.scalars().all()

    return [
        {
            "id": str(log.id),
            "logged_at": log.logged_at.isoformat(),
            "level": log.level,
            "account_id": str(log.account_id) if log.account_id else None,
            "module": log.module,
            "message": log.message,
            "exception": log.exception,
            "extra": log.extra,
        }
        for log in logs
    ]


@router.get("/persistent/stats")
@limiter.limit("60/minute")
async def get_persistent_log_stats(
    request: Request,
    _admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> dict:
    """Get aggregated stats for persistent logs over the given period."""
    from datetime import UTC, timedelta

    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from app.models.persistent_log import PersistentLog

    cutoff = datetime.now(UTC) - timedelta(days=days)

    result = await session.execute(
        sa_select(
            PersistentLog.level,
            func.count().label("count"),
        )
        .where(PersistentLog.logged_at >= cutoff)
        .group_by(PersistentLog.level)
    )

    stats = {row.level: row.count for row in result.all()}
    return {
        "period_days": days,
        "errors": stats.get("ERROR", 0),
        "criticals": stats.get("CRITICAL", 0),
        "total": sum(stats.values()),
    }


# ---------------------------------------------------------------------------
# Account-specific log endpoint (catch-all, must be AFTER /persistent routes)
# ---------------------------------------------------------------------------


@router.get("/{account_id}")
@limiter.limit("60/minute")
async def get_account_logs(
    account_id: uuid.UUID,
    request: Request,
    _admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    level: Annotated[str | None, Query(pattern="^(INFO|WARNING|ERROR|CRITICAL)$")] = None,
    visibility: Annotated[str | None, Query(pattern="^(user|admin)$")] = None,
) -> list[dict]:
    """
    Returns log entries for the given account_id.

    - visibility=user: queries PersistentLog DB table (survives server restarts),
      filtered by _USER_VISIBLE_RE patterns.
    - visibility=admin or default: returns in-memory log buffer (real-time).
    """
    if visibility == "user":
        from sqlalchemy import select as sa_select

        from app.models.persistent_log import PersistentLog

        stmt = sa_select(PersistentLog).where(PersistentLog.account_id == account_id)
        if level:
            stmt = stmt.where(PersistentLog.level == level)
        stmt = stmt.order_by(PersistentLog.logged_at.desc()).limit(limit)

        result = await session.execute(stmt)
        logs = result.scalars().all()

        # Filter by user-visible patterns and map to frontend-expected format
        results = []
        for log in logs:
            if not _USER_VISIBLE_RE.search(log.message):
                continue
            results.append(
                {
                    "ts": log.logged_at.isoformat(),
                    "level": log.level,
                    "module": log.module or "",
                    "msg": log.message,
                }
            )

        # Merge in-memory scan logs (not persisted to DB, but useful for user)
        _SCAN_RE = re.compile(r"스캔 중:")
        mem_logs = log_buffer.get_logs(account_id=str(account_id), level=level, limit=limit)
        for entry in mem_logs:
            msg = entry.get("msg", "")
            if _SCAN_RE.search(msg):
                results.append(entry)

        # Sort merged results by timestamp descending, then trim to limit
        results.sort(key=lambda x: x.get("ts", ""), reverse=True)
        return results[:limit]

    # Default / admin: in-memory buffer (real-time logs)
    results = log_buffer.get_logs(account_id=str(account_id), level=level, limit=limit)
    return results
