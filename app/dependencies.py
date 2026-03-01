from uuid import UUID

from fastapi import Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_trading_session

limiter = Limiter(key_func=get_remote_address)


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


async def get_owned_account(
    account_id: UUID,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_trading_session),
):
    """계정 조회 + 소유권 검증을 한번에 수행. 통과하면 TradingAccount 반환."""
    from app.db.account_repo import AccountRepository
    repo = AccountRepository(session)
    account = await repo.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if str(account.owner_id) != user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
    return account


def get_auth_service(request: Request):
    """Get AuthService from app state"""
    return request.app.state.auth_service


def get_session_manager(request: Request):
    """Get SessionManager from app state"""
    return request.app.state.session_manager


def get_encryption(request: Request):
    """Get EncryptionManager from app state"""
    return request.app.state.encryption
