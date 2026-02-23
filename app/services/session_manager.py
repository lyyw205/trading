from __future__ import annotations
import logging
from itsdangerous import URLSafeTimedSerializer

logger = logging.getLogger(__name__)


class SessionManager:
    """
    서버사이드 세션 관리.
    - 토큰을 암호화된 쿠키에 저장 (itsdangerous 서명)
    - httponly=True, secure=True (운영환경), samesite="lax"
    - max_age = 7일
    """

    def __init__(self, secret_key: str):
        self._serializer = URLSafeTimedSerializer(secret_key)
        self.cookie_name = "session"
        self.max_age = 7 * 24 * 3600  # 7 days

    def create_session_cookie(self, access_token: str, refresh_token: str) -> str:
        """Encode tokens into a signed cookie value"""
        payload = {"at": access_token, "rt": refresh_token}
        return self._serializer.dumps(payload)

    def read_session_cookie(self, cookie_value: str) -> dict | None:
        """Decode and verify cookie, return {"at": ..., "rt": ...} or None"""
        try:
            return self._serializer.loads(cookie_value, max_age=self.max_age)
        except Exception:
            return None
