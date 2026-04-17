from __future__ import annotations

import logging
import time

from itsdangerous import URLSafeTimedSerializer

logger = logging.getLogger(__name__)


class SessionManager:
    """
    서버사이드 세션 관리.
    - uid+role+iat를 서명된 쿠키에 저장 (itsdangerous 서명)
    - httponly=True, secure=True (운영환경), samesite="lax"
    - max_age = 8시간
    - iat: 세션 발급 시각 (비밀번호 변경 시 기존 세션 무효화용)
    """

    # Hard cutoff: legacy cookies without iat are rejected after this date.
    # Set to 24h after the iat feature deployment date (2026-04-17).
    _IAT_CUTOFF_TS = 1776556800  # 2026-04-19T00:00:00Z

    def __init__(self, secret_keys: str | list[str]):
        if isinstance(secret_keys, str):
            secret_keys = [secret_keys]
        self._serializer = URLSafeTimedSerializer(secret_keys)
        self.cookie_name = "session"
        self.max_age = 8 * 3600  # 8 hours

    def create_session_cookie(self, user_id: str, role: str, **_kwargs) -> str:
        """Encode uid+role+iat into a signed cookie value (email excluded for PII minimization)"""
        payload = {"uid": user_id, "role": role, "iat": int(time.time())}
        return self._serializer.dumps(payload)

    def read_session_cookie(self, cookie_value: str) -> dict | None:
        """Decode and verify cookie. Returns {"uid", "role"} or None.
        Returns None for legacy {"at", "rt"} format (graceful transition)."""
        try:
            data = self._serializer.loads(cookie_value, max_age=self.max_age)
            # Reject legacy Supabase token format
            if "at" in data and "rt" in data:
                return None
            if "uid" not in data:
                return None
            return data
        except Exception:
            return None
