from __future__ import annotations
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from app.schemas.auth import UserResponse, LoginUrlResponse
from app.dependencies import limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/google", response_model=LoginUrlResponse)
@limiter.limit("10/minute")
async def google_login(request: Request):
    """Get Google OAuth login URL"""
    auth_service = request.app.state.auth_service
    callback_url = str(request.url_for("auth_callback"))
    url = auth_service.get_google_oauth_url(callback_url)
    return LoginUrlResponse(url=url)


@router.get("/callback", name="auth_callback")
@limiter.limit("10/minute")
async def auth_callback(request: Request, code: str = ""):
    """OAuth callback - exchange code for session"""
    if not code:
        raise HTTPException(status_code=400, detail="Missing auth code")

    auth_service = request.app.state.auth_service
    session_mgr = request.app.state.session_manager

    result = await auth_service.exchange_code_for_session(code)
    if not result:
        raise HTTPException(status_code=401, detail="Authentication failed")

    # Ensure user profile exists
    user = result["user"]
    await auth_service.ensure_user_profile(user["id"], user["email"])

    # Create session cookie
    cookie_value = session_mgr.create_session_cookie(
        result["access_token"], result["refresh_token"]
    )

    response = RedirectResponse(url="/accounts", status_code=302)
    is_secure = not request.app.state.settings_debug
    response.set_cookie(
        key=session_mgr.cookie_name,
        value=cookie_value,
        max_age=session_mgr.max_age,
        httponly=True,
        secure=is_secure,
        samesite="lax",
    )
    return response


@router.post("/logout")
@limiter.limit("10/minute")
async def logout(request: Request):
    """Clear session cookie"""
    session_mgr = request.app.state.session_manager
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(key=session_mgr.cookie_name)
    return response


@router.get("/me", response_model=UserResponse)
@limiter.limit("10/minute")
async def me(request: Request):
    """Get current authenticated user"""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return UserResponse(id=user["id"], email=user["email"], role=user.get("role", "user"))
