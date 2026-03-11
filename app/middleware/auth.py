from __future__ import annotations

import time

from starlette.responses import JSONResponse


class LazyAuthMiddleware:
    """Cookie session -> user injection middleware (로컬 인증 기반).

    DB 검증: 쿠키 payload만 신뢰하지 않고 DB에서 최신 role/is_active를 확인.
    TTL 60초 캐시로 매 요청마다 DB 조회하지 않음.
    """

    # Exact-match public paths (no prefix matching)
    _PUBLIC_EXACT = {"/", "/health", "/login", "/api/auth/login", "/favicon.ico"}
    # Prefix-match public paths (trailing slash prevents overmatch)
    _PUBLIC_PREFIX = ("/static/", "/api/auth/")
    _USER_CACHE_TTL = 60  # seconds
    _USER_CACHE_MAX_SIZE = 200  # max entries to prevent unbounded growth
    _user_cache: dict[str, tuple[float, dict | None]] = {}

    def __init__(self, app):
        self.app = app

    @classmethod
    def evict_user_cache(cls, uid: str) -> None:
        """Remove a specific user from the auth cache (e.g. on deactivation)."""
        cls._user_cache.pop(uid, None)

    async def _validate_user_from_db(self, app_state, uid: str) -> dict | None:
        """DB에서 사용자 조회 (TTL 캐시). 비활성/삭제 → None."""
        now = time.time()
        cached = self._user_cache.get(uid)
        if cached and (now - cached[0]) < self._USER_CACHE_TTL:
            return cached[1]

        auth_service = getattr(app_state.state, "auth_service", None)
        if not auth_service:
            return None

        db_user = await auth_service.get_user_by_id(uid)
        # Evict oldest entries when cache exceeds max size
        if len(self._user_cache) >= self._USER_CACHE_MAX_SIZE:
            oldest_key = min(self._user_cache, key=lambda k: self._user_cache[k][0])
            del self._user_cache[oldest_key]
        self._user_cache[uid] = (now, db_user)
        return db_user

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        from starlette.requests import Request

        request = Request(scope, receive)
        path = request.url.path

        # Public paths - skip auth
        if path in self._PUBLIC_EXACT or any(path.startswith(p) for p in self._PUBLIC_PREFIX):
            await self.app(scope, receive, send)
            return

        # Get services from app state (set during lifespan)
        # fail-closed: 서비스 미초기화 시 503 반환
        app_state = scope.get("app")
        if not app_state:
            response = JSONResponse({"detail": "Service initializing"}, status_code=503)
            await response(scope, receive, send)
            return

        session_manager = getattr(app_state.state, "session_manager", None)
        if not session_manager:
            response = JSONResponse({"detail": "Service initializing"}, status_code=503)
            await response(scope, receive, send)
            return

        # Extract session cookie
        cookie_value = request.cookies.get(session_manager.cookie_name)
        if not cookie_value:
            response = self._unauthorized(path)
            await response(scope, receive, send)
            return

        session_data = session_manager.read_session_cookie(cookie_value)
        if not session_data:
            # Legacy {"at","rt"} format or invalid → force logout
            is_secure = not getattr(app_state.state, "settings_debug", False)
            response = self._force_logout(path, session_manager, is_secure=is_secure)
            await response(scope, receive, send)
            return

        # DB validation: 최신 role/is_active 확인
        uid = session_data["uid"]
        db_user = await self._validate_user_from_db(app_state, uid)
        if not db_user:
            # 비활성/삭제된 사용자 → 강제 로그아웃
            is_secure = not getattr(app_state.state, "settings_debug", False)
            response = self._force_logout(path, session_manager, is_secure=is_secure)
            await response(scope, receive, send)
            return

        # DB에서 가져온 최신 정보 사용 (쿠키의 role이 아닌 DB의 role)
        user = {
            "id": db_user["id"],
            "email": db_user["email"],
            "role": db_user["role"],
        }

        # Inject user into scope state
        scope.setdefault("state", {})
        scope["state"]["user"] = user

        await self.app(scope, receive, send)

    def _unauthorized(self, path: str):
        from starlette.responses import JSONResponse, RedirectResponse

        if path.startswith("/api/"):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return RedirectResponse(url="/login", status_code=302)

    def _force_logout(self, path: str, session_manager, *, is_secure: bool = True):
        """Legacy session format detected → clear cookie and redirect."""
        from starlette.responses import RedirectResponse

        response = RedirectResponse(url="/login", status_code=302)
        response.delete_cookie(
            key=session_manager.cookie_name,
            path="/",
            httponly=True,
            secure=is_secure,
            samesite="lax",
        )
        return response
