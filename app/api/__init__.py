from app.api.accounts import router as accounts_router
from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.backtest import router as backtest_router
from app.api.combos import router as combos_router
from app.api.dashboard import router as dashboard_router
from app.api.health import router as health_router

__all__ = [
    "accounts_router",
    "admin_router",
    "auth_router",
    "backtest_router",
    "combos_router",
    "dashboard_router",
    "health_router",
]
