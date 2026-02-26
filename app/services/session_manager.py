from __future__ import annotations
import logging
from itsdangerous import URLSafeTimedSerializer

logger = logging.getLogger(__name__)


class SessionManager:
    """
    서버사이드 세션 관리.
    - uid+email+role을 암호화된 쿠키에 저장 (itsdangerous 서명)
    - httponly=True, secure=True (운영환경), samesite="lax"
    - max_age = 7일
    """

    def __init__(self, secret_key: str):
        self._serializer = URLSafeTimedSerializer(secret_key)
        self.cookie_name = "session"
        self.max_age = 7 * 24 * 3600  # 7 days

    def create_session_cookie(self, user_id: str, email: str, role: str) -> str:
        """Encode uid+email+role into a signed cookie value"""
        payload = {"uid": user_id, "email": email, "role": role}
        return self._serializer.dumps(payload)

    def read_session_cookie(self, cookie_value: str) -> dict | None:
        """Decode and verify cookie. Returns {"uid", "email", "role"} or None.
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
