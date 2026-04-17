from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import get_current_user, limiter
from app.schemas.auth import ChangePasswordRequest
from app.schemas.common import MessageResponse

router = APIRouter(prefix="/api/user", tags=["user"])


@router.post("/change-password", response_model=MessageResponse)
@limiter.limit("5/minute")
async def change_password(
    req: ChangePasswordRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """현재 비밀번호를 확인한 뒤 새 비밀번호로 변경."""
    auth_service = request.app.state.auth_service

    # 현재 비밀번호 검증 (brute-force 보호 자동 적용)
    verified = await auth_service.authenticate(user["email"], req.current_password)
    if not verified:
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    try:
        success = await auth_service.reset_password(user["id"], req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    # 비밀번호 변경 후 auth 캐시 evict → 기존 세션이 즉시 password_changed_at 재검증
    from app.middleware.auth import LazyAuthMiddleware

    LazyAuthMiddleware.evict_user_cache(user["id"])

    return {"status": "password_changed"}
