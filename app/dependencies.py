from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_trading_session


def get_trading_engine(request: Request):
    """Get the TradingEngine from app state"""
    return request.app.state.trading_engine


async def get_db(session: AsyncSession = Depends(get_trading_session)):
    """DB 세션 의존성 주입."""
    yield session


async def get_current_user(request: Request) -> dict:
    """현재 인증된 사용자 정보 반환. 미인증 시 401."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    return user


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """관리자 권한 확인. 비관리자 시 403."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return user


def get_auth_service(request: Request):
    """Get AuthService from app state"""
    return request.app.state.auth_service


def get_session_manager(request: Request):
    """Get SessionManager from app state"""
    return request.app.state.session_manager


def get_encryption(request: Request):
    """Get EncryptionManager from app state"""
    return request.app.state.encryption
