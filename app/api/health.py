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
    """Liveness probe — returns minimal status for Docker/infra healthchecks."""
    db_check = await _check_database()

    alerts: list[str] = []
    overall = "healthy"

    if db_check["status"] != "ok":
        overall = "unhealthy"
        alerts.append("database_unreachable")

    # Check trading engine circuit breakers if available
    engine = getattr(request.app.state, "trading_engine", None)
    if engine:
        try:
            health = engine.get_account_health()
            if any(v.get("circuit_breaker_tripped") for v in health.values()):
                overall = "degraded"
                alerts.append("circuit_breaker_active")
        except Exception:
            pass

    return {
        "status": overall,
        "version": "0.1.0",
        "uptime_seconds": round(time.monotonic() - _start_time, 1),
        "checks": {
            "database": db_check,
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
        return {"status": "error", "error": "database_unreachable"}
