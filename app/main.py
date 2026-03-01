from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi.errors import RateLimitExceeded
from starlette.responses import JSONResponse
from starlette_csrf import CSRFMiddleware

from app.api.accounts import router as accounts_router
from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.backtest import router as backtest_router
from app.api.combos import router as combos_router
from app.api.dashboard import router as dashboard_router
from app.api.admin_db import router as admin_db_router
from app.api.debug import router as debug_router
from app.api.health import router as health_router
from app.api.metrics import router as metrics_router
from app.config import GlobalConfig
from app.dashboard.routes import router as pages_router
from app.db.session import TradingSessionLocal
from app.dependencies import limiter
from app.middleware.csrf_middleware import CSRF_EXEMPT_PATHS
from app.middleware.request_id import RequestIdMiddleware
from app.services.auth_service import AuthService
from app.services.rate_limiter import GlobalRateLimiter
from app.services.session_manager import SessionManager
from app.services.trading_engine import TradingEngine
from app.utils.encryption import EncryptionManager
from app.utils.logging import setup_logging

settings = GlobalConfig()


def _filter_sensitive_data(event, hint):
    """Strip sensitive data from Sentry events."""
    if "request" in event:
        headers = event["request"].get("headers", {})
        for key in list(headers.keys()):
            if key.lower() in ("cookie", "authorization", "x-api-key", "x-csrf-token"):
                headers[key] = "[FILTERED]"
    return event


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize Sentry before anything else
    if settings.sentry_dsn:
        import sentry_sdk
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=0.2,
            profiles_sample_rate=0.1,
            send_default_pii=False,
            with_locals=False,  # CRITICAL: prevent API key leakage in stack frames
            before_send=_filter_sensitive_data,
        )

    # Setup logging
    setup_logging(settings.log_level)
    logger = logging.getLogger(__name__)
    logger.info("Starting crypto-multi-trader...")

    # Thread pool for asyncio.to_thread (Binance sync calls)
    loop = asyncio.get_running_loop()
    pool_size = max(10, settings.thread_pool_size)
    loop.set_default_executor(ThreadPoolExecutor(max_workers=pool_size))

    # Store debug flag for cookie secure setting
    app.state.settings_debug = settings.debug

    # Initialize encryption
    encryption = EncryptionManager(settings.encryption_key_list)
    app.state.encryption = encryption

    # Initialize auth service (local DB-based auth)
    auth_service = AuthService(session_factory=TradingSessionLocal)
    app.state.auth_service = auth_service

    # Initialize session manager
    session_manager = SessionManager(settings.session_secret_key)
    app.state.session_manager = session_manager

    # Initialize trading engine
    rate_limiter = GlobalRateLimiter(max_rate=settings.api_rate_limit)
    engine = TradingEngine(rate_limiter=rate_limiter, encryption=encryption)
    app.state.trading_engine = engine

    # Start trading (non-blocking)
    engine_task = asyncio.create_task(engine.start())

    # Start candle aggregation background job
    from app.services.candle_aggregator import run_aggregation_loop
    aggregation_task = asyncio.create_task(run_aggregation_loop())

    # Auto-bootstrap initial admin if configured and not yet created
    if settings.initial_admin_email and settings.initial_admin_password:
        try:
            bootstrap_user = await auth_service.create_user(
                email=settings.initial_admin_email,
                password=settings.initial_admin_password,
                role="admin",
            )
            logger.info("Initial admin created: %s", bootstrap_user["email"])
        except ValueError:
            pass  # Already exists, skip silently

    # Clean up orphan RUNNING backtests from previous crashes
    try:
        from sqlalchemy import update

        from app.models.backtest_run import BacktestRun

        async with TradingSessionLocal() as bt_session:
            stmt = (
                update(BacktestRun)
                .where(BacktestRun.status.in_(["RUNNING", "PENDING"]))
                .values(status="FAILED", error_message="Server restarted during execution")
            )
            result = await bt_session.execute(stmt)
            if result.rowcount > 0:
                logger.info("Marked %d orphan backtests as FAILED", result.rowcount)
            await bt_session.commit()
    except Exception as e:
        logger.warning("Failed to clean up orphan backtests: %s", e)

    yield

    # Shutdown
    logger.info("Shutting down trading engine...")
    aggregation_task.cancel()
    engine_task.cancel()
    await engine.stop_all()
    logger.info("Trading engine stopped")


app = FastAPI(
    title="Crypto Multi-Trader",
    description="Multi-account Bitcoin auto-trading bot",
    version="0.1.0",
    lifespan=lifespan,
)

# Rate limiting (slowapi)
app.state.limiter = limiter


def _rate_limit_handler(request, exc: RateLimitExceeded):
    return JSONResponse(
        {"detail": f"Rate limit exceeded: {exc.detail}"},
        status_code=429,
        headers={"Retry-After": "60"},
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

# Mount static files
import os

static_dir = os.path.join(os.path.dirname(__file__), "dashboard", "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Template directory
templates_dir = os.path.join(os.path.dirname(__file__), "dashboard", "templates")
os.makedirs(templates_dir, exist_ok=True)
app.state.templates = Jinja2Templates(directory=templates_dir)

# Middleware (order matters: last added = first executed)
# CSRF must be before AuthMiddleware so CSRF check happens on authenticated requests
app.add_middleware(
    CSRFMiddleware,
    secret=settings.csrf_secret,
    exempt_urls=CSRF_EXEMPT_PATHS,
)


# AuthMiddleware added via on_startup because it needs app.state objects
# We use a custom startup event approach with add_middleware
# Note: AuthMiddleware is added after app creation but needs session_manager/auth_service
# These are set in lifespan, so we use a lazy wrapper
class LazyAuthMiddleware:
    """Cookie session -> user injection middleware (로컬 인증 기반)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        from starlette.requests import Request

        request = Request(scope, receive)
        path = request.url.path

        # Public paths - skip auth
        public_paths = {"/health", "/login", "/api/auth/login", "/static", "/favicon.ico"}
        if any(path.startswith(p) for p in public_paths) or path == "/":
            await self.app(scope, receive, send)
            return

        # Get services from app state (set during lifespan)
        app_state = scope.get("app")
        if not app_state:
            await self.app(scope, receive, send)
            return

        session_manager = getattr(app_state.state, "session_manager", None)
        if not session_manager:
            await self.app(scope, receive, send)
            return

        # Extract session cookie
        cookie_value = request.cookies.get(session_manager.cookie_name)
        if not cookie_value:
            response = self._unauthorized(path)
            await response(scope, receive, send)
            return

        session_data = session_manager.read_session_cookie(cookie_value)
        if not session_data:
            # Legacy {"at","rt"} format or invalid → force logout
            response = self._force_logout(path, session_manager)
            await response(scope, receive, send)
            return

        # session_data = {"uid": ..., "email": ..., "role": ...}
        # Map uid → id for downstream compatibility
        user = {
            "id": session_data["uid"],
            "email": session_data["email"],
            "role": session_data.get("role", "user"),
        }

        # Inject user into scope state
        scope.setdefault("state", {})
        scope["state"]["user"] = user

        await self.app(scope, receive, send)

    def _unauthorized(self, path: str):
        from starlette.responses import JSONResponse, RedirectResponse
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return RedirectResponse(url="/login", status_code=302)

    def _force_logout(self, path: str, session_manager):
        """Legacy session format detected → clear cookie and redirect."""
        from starlette.responses import RedirectResponse
        response = RedirectResponse(url="/login", status_code=302)
        response.delete_cookie(key=session_manager.cookie_name)
        return response


app.add_middleware(LazyAuthMiddleware)
app.add_middleware(RequestIdMiddleware)

# Include API routers
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(combos_router)
app.include_router(dashboard_router)
app.include_router(admin_router)
app.include_router(backtest_router)
app.include_router(debug_router)
app.include_router(metrics_router)
app.include_router(admin_db_router)

# Include SSR page routes (must be after API routers)
app.include_router(pages_router)


@app.get("/")
async def root():
    return {"status": "running", "service": "crypto-multi-trader"}
