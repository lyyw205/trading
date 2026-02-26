from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import GlobalConfig

settings = GlobalConfig()

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


async def get_trading_session() -> AsyncSession:
    """트레이딩 엔진용 DB 세션 (FastAPI Depends 또는 직접 사용)."""
    async with TradingSessionLocal() as session:
        yield session
