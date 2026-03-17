from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.dependencies import limiter
from app.schemas.auth import LoginRequest, LoginResponse, UserResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
@limiter.limit("10/minute")
async def login(login_req: LoginRequest, request: Request):
    """이메일+비밀번호 로그인"""
    auth_service = request.app.state.auth_service
    session_manager = request.app.state.session_manager

    user = await auth_service.authenticate(login_req.email, login_req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    cookie_value = session_manager.create_session_cookie(user_id=user["id"], role=user["role"])

    response = LoginResponse(success=True, user=UserResponse(**user))
    json_resp = JSONResponse(content=response.model_dump())

    is_secure = not request.app.state.settings_debug
    json_resp.set_cookie(
        key=session_manager.cookie_name,
        value=cookie_value,
        max_age=session_manager.max_age,
        httponly=True,
        secure=is_secure,
        samesite="lax",
    )
    return json_resp


@router.post("/logout")
@limiter.limit("10/minute")
async def logout(request: Request):
    """Clear session cookie"""
    session_manager = request.app.state.session_manager
    is_secure = not request.app.state.settings_debug
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(
        key=session_manager.cookie_name,
        path="/",
        httponly=True,
        secure=is_secure,
        samesite="lax",
    )
    return response


@router.get("/me", response_model=UserResponse)
@limiter.limit("10/minute")
async def me(request: Request):
    """Get current authenticated user"""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return UserResponse(id=user["id"], email=user["email"], role=user.get("role", "user"))
