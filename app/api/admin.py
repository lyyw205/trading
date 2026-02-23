from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_trading_session
from app.db.account_repo import AccountRepository
from app.models.user import UserProfile
from app.schemas.account import AccountResponse

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_admin(request: Request):
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/accounts")
async def admin_list_accounts(request: Request, session: AsyncSession = Depends(get_trading_session)):
    _require_admin(request)
    repo = AccountRepository(session)
    accounts = await repo.get_active_accounts()
    engine = request.app.state.trading_engine
    health = engine.get_account_health()
    return [{
        **AccountResponse.model_validate(a).model_dump(),
        "health": health.get(str(a.id), {}),
    } for a in accounts]


@router.get("/users")
async def admin_list_users(request: Request, session: AsyncSession = Depends(get_trading_session)):
    _require_admin(request)
    stmt = select(UserProfile).order_by(UserProfile.created_at)
    result = await session.execute(stmt)
    users = result.scalars().all()
    return [{"id": str(u.id), "email": u.email, "role": u.role, "created_at": str(u.created_at)} for u in users]


@router.get("/overview")
async def admin_overview(request: Request, session: AsyncSession = Depends(get_trading_session)):
    _require_admin(request)
    engine = request.app.state.trading_engine
    stmt_users = select(UserProfile)
    result_users = await session.execute(stmt_users)
    total_users = len(result_users.scalars().all())
    repo = AccountRepository(session)
    all_accounts = await repo.get_active_accounts()
    return {
        "total_users": total_users,
        "total_accounts": len(all_accounts),
        "active_traders": engine.active_account_count,
        "account_health": engine.get_account_health(),
    }


@router.post("/users/{user_id}/role")
async def admin_set_role(user_id: str, request: Request, role: str = "user", session: AsyncSession = Depends(get_trading_session)):
    _require_admin(request)
    if role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Invalid role")
    stmt = select(UserProfile).where(UserProfile.id == UUID(user_id))
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.role = role
    await session.commit()
    return {"status": "updated", "user_id": user_id, "role": role}
