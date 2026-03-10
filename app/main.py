from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi.errors import RateLimitExceeded
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, RedirectResponse
from starlette_csrf import CSRFMiddleware

from app.api.accounts import router as accounts_router
from app.api.admin_accounts import router as admin_accounts_router
from app.api.admin_db import router as admin_db_router
from app.api.admin_trading import router as admin_trading_router
from app.api.admin_users import router as admin_users_router
from app.api.auth import router as auth_router
from app.api.backtest import router as backtest_router
from app.api.combos import router as combos_router
from app.api.dashboard import router as dashboard_router
from app.api.debug import router as debug_router
from app.api.health import router as health_router
from app.api.logs import router as logs_router
from app.api.metrics import router as metrics_router
from app.api.reports import router as reports_router
from app.api.user import router as user_router
from app.config import get_settings
from app.dashboard.routes import router as pages_router
from app.db.session import TradingSessionLocal
from app.dependencies import limiter
from app.middleware.auth import LazyAuthMiddleware
from app.middleware.csrf_middleware import CSRF_EXEMPT_PATHS
from app.middleware.no_cache_html import NoCacheHTMLMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.services.auth_service import AuthService
from app.services.candle_aggregator import run_aggregation_loop
from app.services.rate_limiter import GlobalRateLimiter
from app.services.session_manager import SessionManager
from app.services.trading_engine import TradingEngine
from app.utils.encryption import EncryptionManager
from app.utils.logging import setup_logging

settings = get_settings()

# Constants
MIN_THREAD_POOL_SIZE = 10
RATE_LIMIT_RETRY_AFTER_SEC = "60"
_SENSITIVE_HEADER_NAMES = {"cookie", "authorization", "x-api-key", "x-csrf-token"}
_SENSITIVE_BODY_KEYWORDS = {"password", "secret", "key", "token"}


def _filter_sensitive_data(event, hint):
    """Strip sensitive data from Sentry events (headers + request body)."""
    if "request" in event:
        headers = event["request"].get("headers", {})
        for key in list(headers.keys()):
            if key.lower() in _SENSITIVE_HEADER_NAMES:
                headers[key] = "[FILTERED]"
        data = event["request"].get("data")
        if isinstance(data, dict):
            for key in list(data.keys()):
                if any(s in key.lower() for s in _SENSITIVE_BODY_KEYWORDS):
                    data[key] = "[FILTERED]"
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
    pool_size = max(MIN_THREAD_POOL_SIZE, settings.thread_pool_size)
    executor = ThreadPoolExecutor(max_workers=pool_size)
    loop.set_default_executor(executor)

    # Store debug flag for cookie secure setting
    app.state.settings_debug = settings.debug

    # Initialize encryption
    encryption = EncryptionManager(settings.encryption_key_list)
    app.state.encryption = encryption

    # Initialize auth service (local DB-based auth)
    auth_service = AuthService(session_factory=TradingSessionLocal)
    app.state.auth_service = auth_service

    # Initialize session manager
    session_manager = SessionManager(settings.session_secret_key_list)
    app.state.session_manager = session_manager

    # Initialize alert service (Telegram)
    from app.services.alert_service import AlertService, set_alert_service

    alert_service = AlertService(settings)
    app.state.alert_service = alert_service
    set_alert_service(alert_service)

    # Initialize trading engine
    rate_limiter = GlobalRateLimiter(max_rate=settings.api_rate_limit)
    engine = TradingEngine(rate_limiter=rate_limiter, encryption=encryption)
    app.state.trading_engine = engine

    # Start trading (non-blocking) with error callback
    def _on_engine_error(task: asyncio.Task):
        if not task.cancelled() and task.exception():
            logger.critical("Trading engine crashed: %s", task.exception())

    engine_task = asyncio.create_task(engine.start())
    engine_task.add_done_callback(_on_engine_error)

    # Start candle aggregation background job
    aggregation_task = asyncio.create_task(run_aggregation_loop())

    # Start log persister background task
    from app.services.log_persister import LogPersister
    from app.utils.logging import persist_handler

    log_persister = None
    if persist_handler:
        log_persister = LogPersister(persist_handler.log_queue)
        await log_persister.start()

    # Start daily report scheduler
    from app.services.daily_report_service import run_daily_report_loop

    report_task = asyncio.create_task(run_daily_report_loop(alert_service))

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

    # Stop daily report scheduler
    report_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await report_task

    # Drain log persister
    if log_persister:
        await log_persister.stop()

    # Shutdown
    logger.info("Shutting down trading engine...")
    aggregation_task.cancel()
    engine_task.cancel()
    await engine.stop_all()
    for task in [engine_task, aggregation_task]:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await alert_service.close()
    executor.shutdown(wait=True, cancel_futures=True)
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
        headers={"Retry-After": RATE_LIMIT_RETRY_AFTER_SEC},
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

# Mount static files
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


app.add_middleware(LazyAuthMiddleware)
app.add_middleware(RequestIdMiddleware)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token", "X-CSRFToken"],
)


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(NoCacheHTMLMiddleware)

# Include API routers
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(combos_router)
app.include_router(dashboard_router)
app.include_router(admin_accounts_router)
app.include_router(admin_trading_router)
app.include_router(admin_users_router)
app.include_router(backtest_router)
if settings.debug or settings.environment != "production":
    app.include_router(debug_router)
app.include_router(metrics_router)
app.include_router(admin_db_router)
app.include_router(logs_router)
app.include_router(reports_router)
app.include_router(user_router)

# Include SSR page routes (must be after API routers)
app.include_router(pages_router)


@app.get("/")
async def root():
    return RedirectResponse(url="/login", status_code=302)
