from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_trading_session
from app.dependencies import limiter, require_admin
from app.models.account import TradingAccount
from app.models.user import UserProfile
from app.schemas.auth import CreateUserRequest, ResetPasswordRequest, SetActiveRequest, SetRoleRequest
from app.utils.logging import audit_log

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users")
@limiter.limit("60/minute")
async def admin_list_users(
    request: Request, admin: dict = Depends(require_admin), session: AsyncSession = Depends(get_trading_session)
):
    account_count_sq = (
        select(TradingAccount.owner_id, sa_func.count().label("account_count"))
        .group_by(TradingAccount.owner_id)
        .subquery()
    )
    stmt = (
        select(UserProfile, sa_func.coalesce(account_count_sq.c.account_count, 0).label("account_count"))
        .outerjoin(account_count_sq, UserProfile.id == account_count_sq.c.owner_id)
        .order_by(UserProfile.created_at)
    )
    result = await session.execute(stmt)
    rows = result.all()
    return [
        {
            "id": str(user_profile.id),
            "email": user_profile.email,
            "role": user_profile.role,
            "is_active": user_profile.is_active,
            "created_at": str(user_profile.created_at),
            "account_count": account_count,
        }
        for user_profile, account_count in rows
    ]


@router.put("/users/{user_id}/role")
@limiter.limit("30/minute")
async def admin_set_role(
    user_id: str,
    body: SetRoleRequest,
    request: Request,
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    role = body.role
    stmt = select(UserProfile).where(UserProfile.id == UUID(user_id))
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.role = role
    await session.commit()

    audit_log("admin_role_changed", user_id=admin["id"], target_user=user_id, new_role=role)
    return {"status": "updated", "user_id": user_id, "role": role}


@router.post("/users")
@limiter.limit("10/minute")
async def admin_create_user(req: CreateUserRequest, request: Request, admin: dict = Depends(require_admin)):
    """관리자: 새 사용자 생성"""
    auth_service = request.app.state.auth_service
    try:
        new_user = await auth_service.create_user(req.email, req.password, req.role)
    except ValueError as e:
        if "이미 등록된" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))

    audit_log("admin_user_created", user_id=admin["id"], target_email=req.email, target_role=req.role)
    return {"status": "created", **new_user}


@router.post("/users/{user_id}/reset-password")
@limiter.limit("10/minute")
async def admin_reset_password(
    user_id: str, req: ResetPasswordRequest, request: Request, admin: dict = Depends(require_admin)
):
    """관리자: 사용자 비밀번호 초기화"""
    auth_service = request.app.state.auth_service
    try:
        success = await auth_service.reset_password(user_id, req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    audit_log("admin_password_reset", user_id=admin["id"], target_user=user_id)
    return {"status": "password_reset"}


@router.put("/users/{user_id}/active")
@limiter.limit("10/minute")
async def admin_set_active(user_id: str, req: SetActiveRequest, request: Request, admin: dict = Depends(require_admin)):
    """관리자: 계정 활성/비활성화"""
    auth_service = request.app.state.auth_service
    success = await auth_service.set_user_active(user_id, req.is_active)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    # 비활성화 시 인증 캐시 즉시 무효화 (60초 TTL 우회)
    if not req.is_active:
        from app.middleware.auth import LazyAuthMiddleware

        LazyAuthMiddleware.evict_user_cache(user_id)

    audit_log("admin_user_active_changed", user_id=admin["id"], target_user=user_id, is_active=req.is_active)
    return {"status": "updated", "is_active": req.is_active}
