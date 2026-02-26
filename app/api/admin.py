from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_trading_session
from app.db.account_repo import AccountRepository
from app.models.user import UserProfile
from app.schemas.account import AccountResponse
from app.dependencies import require_admin, get_current_user, limiter
from app.utils.logging import audit_log

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/accounts")
async def admin_list_accounts(request: Request, admin: dict = Depends(require_admin), session: AsyncSession = Depends(get_trading_session)):
    repo = AccountRepository(session)
    accounts = await repo.get_active_accounts()
    engine = request.app.state.trading_engine
    health = engine.get_account_health()
    return [{
        **AccountResponse.model_validate(a).model_dump(),
        "health": health.get(str(a.id), {}),
    } for a in accounts]


@router.get("/users")
async def admin_list_users(admin: dict = Depends(require_admin), session: AsyncSession = Depends(get_trading_session)):
    stmt = select(UserProfile).order_by(UserProfile.created_at)
    result = await session.execute(stmt)
    users = result.scalars().all()
    return [{"id": str(u.id), "email": u.email, "role": u.role, "is_active": u.is_active, "created_at": str(u.created_at)} for u in users]


@router.get("/overview")
async def admin_overview(request: Request, admin: dict = Depends(require_admin), session: AsyncSession = Depends(get_trading_session)):
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


@router.put("/users/{user_id}/role")
async def admin_set_role(user_id: str, request: Request, admin: dict = Depends(require_admin), session: AsyncSession = Depends(get_trading_session)):
    body = await request.json()
    role = body.get("role", "user")
    if role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Invalid role")
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
async def admin_create_user(request: Request, admin: dict = Depends(require_admin)):
    """관리자: 새 사용자 생성"""
    from app.schemas.auth import CreateUserRequest
    body = await request.json()
    try:
        req = CreateUserRequest(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

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
async def admin_reset_password(user_id: str, request: Request, admin: dict = Depends(require_admin)):
    """관리자: 사용자 비밀번호 초기화"""
    from app.schemas.auth import ResetPasswordRequest
    body = await request.json()
    try:
        req = ResetPasswordRequest(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

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
async def admin_set_active(user_id: str, request: Request, admin: dict = Depends(require_admin)):
    """관리자: 계정 활성/비활성화"""
    from app.schemas.auth import SetActiveRequest
    body = await request.json()
    try:
        req = SetActiveRequest(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    auth_service = request.app.state.auth_service
    success = await auth_service.set_user_active(user_id, req.is_active)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    audit_log("admin_user_active_changed", user_id=admin["id"], target_user=user_id, is_active=req.is_active)
    return {"status": "updated", "is_active": req.is_active}
