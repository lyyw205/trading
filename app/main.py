from __future__ import annotations
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette_csrf import CSRFMiddleware

from starlette.responses import JSONResponse

from app.config import GlobalConfig
from app.utils.logging import setup_logging
from app.dependencies import limiter
from slowapi.errors import RateLimitExceeded
from app.services.trading_engine import TradingEngine
from app.services.rate_limiter import GlobalRateLimiter
from app.services.auth_service import AuthService
from app.services.session_manager import SessionManager
from app.utils.encryption import EncryptionManager
from app.middleware.auth_middleware import AuthMiddleware
from app.middleware.csrf_middleware import CSRF_EXEMPT_PATHS
from app.api.health import router as health_router
from app.api.auth import router as auth_router
from app.api.accounts import router as accounts_router
from app.api.strategies import router as strategies_router
from app.api.dashboard import router as dashboard_router
from app.api.admin import router as admin_router
from app.api.backtest import router as backtest_router
from app.dashboard.routes import router as pages_router

settings = GlobalConfig()


@asynccontextmanager
async def lifespan(app: FastAPI):
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

    # Initialize auth service
    auth_service = AuthService(
        supabase_url=settings.supabase_url,
        supabase_anon_key=settings.supabase_anon_key,
        supabase_service_key=settings.supabase_service_role_key,
    )
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

    # Clean up orphan RUNNING backtests from previous crashes
    try:
        from sqlalchemy import update
        from app.db.session import TradingSessionLocal
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
    """Wraps AuthMiddleware to defer session_manager/auth_service access to request time."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        from starlette.requests import Request
        from starlette.responses import JSONResponse, RedirectResponse

        request = Request(scope, receive)
        path = request.url.path

        # Public paths - skip auth
        public_paths = {"/health", "/login", "/api/auth/google", "/api/auth/callback", "/static", "/favicon.ico"}
        if any(path.startswith(p) for p in public_paths) or path == "/":
            await self.app(scope, receive, send)
            return

        # Get services from app state (set during lifespan)
        app_state = scope.get("app")
        if not app_state:
            await self.app(scope, receive, send)
            return

        session_manager = getattr(app_state.state, "session_manager", None)
        auth_service = getattr(app_state.state, "auth_service", None)

        if not session_manager or not auth_service:
            # Services not initialized yet
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
            response = self._unauthorized(path)
            await response(scope, receive, send)
            return

        access_token = session_data.get("at", "")
        refresh_token = session_data.get("rt", "")

        user = await auth_service.get_user_from_token(access_token)
        new_cookie = None

        if not user and refresh_token:
            new_tokens = await auth_service.refresh_session(refresh_token)
            if new_tokens:
                access_token = new_tokens["access_token"]
                refresh_token = new_tokens["refresh_token"]
                user = await auth_service.get_user_from_token(access_token)
                new_cookie = session_manager.create_session_cookie(access_token, refresh_token)

        if not user:
            response = self._unauthorized(path)
            await response(scope, receive, send)
            return

        # Get role
        role = await auth_service.get_user_role(user["id"])
        user["role"] = role

        # Inject user into scope state
        scope.setdefault("state", {})
        scope["state"]["user"] = user
        scope["state"]["access_token"] = access_token

        if new_cookie:
            # Wrap send to inject Set-Cookie header
            original_send = send

            async def send_with_cookie(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    secure_flag = "; Secure" if not scope.get("app").state.settings_debug else ""
                    cookie_val = (
                        f"{session_manager.cookie_name}={new_cookie}; "
                        f"Max-Age={session_manager.max_age}; "
                        f"Path=/; HttpOnly; SameSite=Lax{secure_flag}"
                    )
                    headers.append((b"set-cookie", cookie_val.encode()))
                    message["headers"] = headers
                await original_send(message)

            await self.app(scope, receive, send_with_cookie)
        else:
            await self.app(scope, receive, send)

    def _unauthorized(self, path: str):
        from starlette.responses import JSONResponse, RedirectResponse
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return RedirectResponse(url="/login", status_code=302)


app.add_middleware(LazyAuthMiddleware)

# Include API routers
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(strategies_router)
app.include_router(dashboard_router)
app.include_router(admin_router)
app.include_router(backtest_router)

# Include SSR page routes (must be after API routers)
app.include_router(pages_router)


@app.get("/")
async def root():
    return {"status": "running", "service": "crypto-multi-trader"}
