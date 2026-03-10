from app.api.accounts import router as accounts_router
from app.api.admin_accounts import router as admin_accounts_router
from app.api.admin_trading import router as admin_trading_router
from app.api.admin_users import router as admin_users_router
from app.api.auth import router as auth_router
from app.api.backtest import router as backtest_router
from app.api.combos import router as combos_router
from app.api.dashboard import router as dashboard_router
from app.api.health import router as health_router

__all__ = [
    "accounts_router",
    "admin_accounts_router",
    "admin_trading_router",
    "admin_users_router",
    "auth_router",
    "backtest_router",
    "combos_router",
    "dashboard_router",
    "health_router",
]
