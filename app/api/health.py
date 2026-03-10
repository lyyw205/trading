from __future__ import annotations

import logging
import time

from fastapi import APIRouter
from sqlalchemy import text

from app.db.session import engine_trading

router = APIRouter(tags=["system"])
logger = logging.getLogger(__name__)

@router.get("/health")
async def health_check():
    """Liveness probe — returns minimal status for Docker/infra healthchecks."""
    db_check = await _check_database()

    overall = "healthy" if db_check["status"] == "ok" else "unhealthy"

    return {
        "status": overall,
        "version": "0.1.0",
        "checks": {
            "database": db_check,
        },
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
