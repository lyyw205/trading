from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import get_current_user, limiter
from app.schemas.auth import ChangePasswordRequest

router = APIRouter(prefix="/api/user", tags=["user"])


@router.post("/change-password")
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
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다.")

    try:
        success = await auth_service.reset_password(user["id"], req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    return {"status": "password_changed"}
