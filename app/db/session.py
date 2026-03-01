import logging
import time

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import GlobalConfig

settings = GlobalConfig()
_slow_query_logger = logging.getLogger("db.slow_query")

# 1) 트레이딩 엔진용: SQLAlchemy 직접 PostgreSQL 연결 (RLS 바이패스)
engine_trading = create_async_engine(
    settings.database_url or "postgresql+asyncpg://localhost/crypto_trader",
    pool_size=30,
    max_overflow=10,
    echo=settings.debug,
)

TradingSessionLocal = async_sessionmaker(
    engine_trading, class_=AsyncSession, expire_on_commit=False
)


# Slow query detection
@event.listens_for(engine_trading.sync_engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    conn.info["query_start_time"] = time.monotonic()


@event.listens_for(engine_trading.sync_engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    elapsed = time.monotonic() - conn.info.get("query_start_time", 0)
    threshold_sec = settings.slow_query_threshold_ms / 1000.0
    if elapsed > threshold_sec:
        _slow_query_logger.warning(
            "SLOW_QUERY duration_ms=%.1f query=%s",
            elapsed * 1000,
            statement[:200],
        )


async def get_trading_session() -> AsyncSession:
    """트레이딩 엔진용 DB 세션 (FastAPI Depends 또는 직접 사용)."""
    async with TradingSessionLocal() as session:
        yield session
