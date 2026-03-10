"""Root conftest — DB fixtures, app factory, common test infrastructure."""

import os
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Test database URL (Docker test-db on port 5433)
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5433/crypto_trader_test",
)


def _test_db_available() -> bool:
    """Check if test database is reachable via TCP socket probe."""
    import socket

    try:
        sock = socket.create_connection(("localhost", 5433), timeout=1.0)
        sock.close()
        return True
    except OSError:
        return False


_db_available = None


def _is_db_available() -> bool:
    global _db_available
    if _db_available is None:
        _db_available = _test_db_available()
    return _db_available


def _make_engine():
    """Create a disposable async engine with NullPool (no cross-loop issues)."""
    return create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _init_test_db():
    """Create/drop tables once per session. NOT shared with test fixtures."""
    if not _is_db_available():
        pytest.skip("Test database not available (start with: docker compose --profile test up -d test-db)")

    engine = _make_engine()
    from app.models.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    yield

    engine = _make_engine()
    from app.models.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(_init_test_db):
    """
    Per-test async DB session with SAVEPOINT rollback.

    Creates its own engine in the test's event loop to avoid cross-loop issues.
    Patches TradingSessionLocal so service code uses this same connection,
    ensuring all writes are rolled back after each test.
    """
    engine = _make_engine()
    async with engine.connect() as conn:
        trans = await conn.begin()

        TestSessionLocal = async_sessionmaker(bind=conn, class_=AsyncSession, expire_on_commit=False)

        nested = await conn.begin_nested()
        session = TestSessionLocal()

        @event.listens_for(session.sync_session, "after_transaction_end")
        def restart_savepoint(session_sync, transaction):
            if transaction.nested and not transaction._parent.nested:
                session_sync.begin_nested()

        with patch("app.db.session.TradingSessionLocal", TestSessionLocal):
            yield session

        await session.close()
        if nested.is_active:
            await nested.rollback()
        await trans.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session_factory(_init_test_db):
    """
    Per-test async session factory with SAVEPOINT rollback.
    Use for services that create their own sessions (e.g., AuthService).
    """
    engine = _make_engine()
    async with engine.connect() as conn:
        trans = await conn.begin()
        nested = await conn.begin_nested()

        TestSessionLocal = async_sessionmaker(bind=conn, class_=AsyncSession, expire_on_commit=False)

        original_call = TestSessionLocal.__call__

        def _patched_call(*args, **kwargs):
            session = original_call(*args, **kwargs)

            @event.listens_for(session.sync_session, "after_transaction_end")
            def restart_savepoint(session_sync, transaction):
                if transaction.nested and not transaction._parent.nested:
                    session_sync.begin_nested()

            return session

        TestSessionLocal.__call__ = _patched_call

        with patch("app.db.session.TradingSessionLocal", TestSessionLocal):
            yield TestSessionLocal

        if nested.is_active:
            await nested.rollback()
        await trans.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def app_client(db_session):
    """FastAPI test client with DB session override and auth bypass."""
    from httpx import ASGITransport, AsyncClient

    from app.config import GlobalConfig
    from app.db.session import get_trading_session
    from app.main import app
    from app.services.session_manager import SessionManager

    TestSessionLocal = async_sessionmaker(bind=db_session.get_bind(), class_=AsyncSession, expire_on_commit=False)

    async def override_get_session():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_trading_session] = override_get_session

    # Initialize session_manager on app state (normally done in lifespan)
    settings = GlobalConfig()
    app.state.session_manager = SessionManager(settings.session_secret_key_list)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
def backtest_client():
    """Pre-configured BacktestClient for testing."""
    from app.exchange.backtest_client import BacktestClient

    return BacktestClient(
        symbol="BTCUSDT",
        initial_balance_usdt=10000.0,
        initial_balance_btc=0.0,
    )


@pytest.fixture
def mock_encryption():
    """EncryptionManager with deterministic test keys."""
    from cryptography.fernet import Fernet

    from app.utils.encryption import EncryptionManager

    test_key = Fernet.generate_key().decode()
    return EncryptionManager([test_key])
