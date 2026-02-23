from __future__ import annotations
from fastapi import APIRouter, Request

router = APIRouter(tags=["system"])


@router.get("/health")
async def health_check(request: Request):
    """System health check with per-account status"""
    engine = getattr(request.app.state, "trading_engine", None)
    if engine is None:
        return {"status": "starting", "accounts": {}}

    account_health = engine.get_account_health()
    all_healthy = all(h.get("running", False) for h in account_health.values()) if account_health else True

    return {
        "status": "healthy" if all_healthy else "degraded",
        "active_accounts": engine.active_account_count,
        "accounts": account_health,
    }
