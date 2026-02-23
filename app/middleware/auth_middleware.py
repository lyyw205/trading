from __future__ import annotations
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, RedirectResponse

logger = logging.getLogger(__name__)

# Paths that don't require auth
PUBLIC_PATHS = {"/", "/health", "/login", "/api/auth/google", "/api/auth/callback", "/static"}


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Cookie session -> user injection middleware.
    1. Extract session cookie
    2. Decode tokens via SessionManager
    3. Validate access_token via Supabase
    4. Auto-refresh if expired
    5. Set request.state.user and request.state.access_token
    6. Redirect to /login if auth fails (SSR) or 401 (API)
    """

    def __init__(self, app, session_manager, auth_service):
        super().__init__(app)
        self._session_manager = session_manager
        self._auth_service = auth_service

    async def dispatch(self, request: Request, call_next):
        # Check if path is public
        path = request.url.path
        if any(path.startswith(p) for p in PUBLIC_PATHS):
            return await call_next(request)

        # Extract session cookie
        cookie_value = request.cookies.get(self._session_manager.cookie_name)
        if not cookie_value:
            return self._unauthorized(request)

        session_data = self._session_manager.read_session_cookie(cookie_value)
        if not session_data:
            return self._unauthorized(request)

        access_token = session_data.get("at", "")
        refresh_token = session_data.get("rt", "")

        # Validate token
        user = await self._auth_service.get_user_from_token(access_token)
        response = None

        if not user and refresh_token:
            # Try refreshing
            new_tokens = await self._auth_service.refresh_session(refresh_token)
            if new_tokens:
                access_token = new_tokens["access_token"]
                refresh_token = new_tokens["refresh_token"]
                user = await self._auth_service.get_user_from_token(access_token)

                # Will set new cookie on response
                response = await call_next(request)
                new_cookie = self._session_manager.create_session_cookie(access_token, refresh_token)
                response.set_cookie(
                    key=self._session_manager.cookie_name,
                    value=new_cookie,
                    max_age=self._session_manager.max_age,
                    httponly=True,
                    samesite="lax",
                )

        if not user:
            return self._unauthorized(request)

        # Inject user into request state
        request.state.user = user
        request.state.access_token = access_token
        # Get role
        role = await self._auth_service.get_user_role(user["id"])
        request.state.user["role"] = role

        if response is None:
            response = await call_next(request)
        return response

    def _unauthorized(self, request: Request):
        if request.url.path.startswith("/api/"):
            from starlette.responses import JSONResponse
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return RedirectResponse(url="/login", status_code=302)
