from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Request
from sqlalchemy import text

from app.db.session import engine_trading

router = APIRouter(tags=["system"])
logger = logging.getLogger(__name__)

_start_time = time.monotonic()


@router.get("/health")
async def health_check(request: Request):
    """Comprehensive system health check."""
    engine = getattr(request.app.state, "trading_engine", None)

    # DB connectivity check
    db_check = await _check_database()

    # Trading engine status
    if engine is None:
        account_summary = {}
    else:
        account_health = engine.get_account_health()
        running = sum(1 for h in account_health.values() if h.get("running"))
        paused = sum(1 for h in account_health.values() if not h.get("running"))
        cb_tripped = sum(1 for h in account_health.values() if h.get("circuit_breaker_tripped"))
        account_summary = {
            "total": len(account_health),
            "running": running,
            "paused": paused,
            "circuit_breaker_tripped": cb_tripped,
        }

    # Determine overall status
    overall = "healthy"
    alerts = []
    if db_check["status"] != "ok":
        overall = "unhealthy"
        alerts.append("database_unreachable")
    elif account_summary.get("circuit_breaker_tripped", 0) > 0:
        overall = "degraded"
        alerts.append("circuit_breaker_active")

    return {
        "status": overall,
        "version": "0.1.0",
        "uptime_seconds": round(time.monotonic() - _start_time, 1),
        "checks": {
            "database": db_check,
            "accounts": account_summary,
        },
        "alerts": alerts,
    }


async def _check_database() -> dict:
    """Check database connectivity and measure latency."""
    try:
        start = time.monotonic()
        async with engine_trading.connect() as conn:
            await conn.execute(text("SELECT 1"))
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"status": "ok", "latency_ms": latency_ms}
    except Exception as e:
        logger.error("Database health check failed: %s", e)
        return {"status": "error", "error": str(e)}
