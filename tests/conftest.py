"""Root conftest — DB fixtures, app factory, common test infrastructure."""
import asyncio
import os
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Test database URL (Docker test-db on port 5433)
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5433/crypto_trader_test",
)


@pytest.fixture(scope="session")
def event_loop():
    """Create a session-scoped event loop for async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


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


@pytest_asyncio.fixture(scope="session")
async def test_db_engine():
    """Create test database engine and run Alembic migrations once per session."""
    if not _is_db_available():
        pytest.skip("Test database not available (start with: docker compose --profile test up -d test-db)")

    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    # Run Alembic migrations for schema setup
    from alembic.config import Config

    from alembic import command

    # Alembic needs sync URL
    sync_url = TEST_DATABASE_URL.replace("+asyncpg", "")
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(alembic_cfg, "head")

    yield engine

    # Cleanup: drop all tables
    from app.models.base import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_db_engine):
    """
    Per-test async DB session with SAVEPOINT rollback.

    CRITICAL: Patches TradingSessionLocal so service code uses this same
    connection, ensuring all writes are rolled back after each test.
    """
    async with test_db_engine.connect() as conn:
        trans = await conn.begin()

        # Create a session factory bound to this specific connection
        TestSessionLocal = async_sessionmaker(
            bind=conn, class_=AsyncSession, expire_on_commit=False
        )

        # Start a nested savepoint so service commit() doesn't end the outer transaction
        nested = await conn.begin_nested()

        session = TestSessionLocal()

        # Auto-restart savepoint after each commit() from service code
        @event.listens_for(session.sync_session, "after_transaction_end")
        def restart_savepoint(session_sync, transaction):
            if transaction.nested and not transaction._parent.nested:
                session_sync.begin_nested()

        # Patch TradingSessionLocal across all modules that import it
        with patch("app.db.session.TradingSessionLocal", TestSessionLocal):
            yield session

        # Cleanup
        await session.close()
        if nested.is_active:
            await nested.rollback()
        await trans.rollback()


@pytest_asyncio.fixture
async def app_client(db_session, test_db_engine):
    """FastAPI test client with DB session override and auth bypass."""
    from httpx import ASGITransport, AsyncClient

    from app.db.session import get_trading_session
    from app.main import app

    # Override DB dependency to use test session
    TestSessionLocal = async_sessionmaker(
        bind=db_session.get_bind(), class_=AsyncSession, expire_on_commit=False
    )

    async def override_get_session():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_trading_session] = override_get_session

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
