"""SessionManager security tests: iat, TTL, legacy cookie handling."""
from __future__ import annotations

import time

import pytest

from app.services.session_manager import SessionManager

_TEST_SECRET = "test-secret-key-for-session-signing"


@pytest.mark.unit
class TestSessionManagerSecurity:
    def test_cookie_contains_iat(self):
        """Session cookie payload includes iat as int timestamp."""
        sm = SessionManager(_TEST_SECRET)
        cookie = sm.create_session_cookie("uid-1", "user")
        data = sm.read_session_cookie(cookie)
        assert data is not None
        assert "iat" in data
        assert isinstance(data["iat"], int)
        assert abs(data["iat"] - int(time.time())) < 5

    def test_max_age_8_hours(self):
        """Session TTL is 8 hours."""
        sm = SessionManager(_TEST_SECRET)
        assert sm.max_age == 8 * 3600

    def test_legacy_at_rt_format_rejected(self):
        """Legacy Supabase token format {at, rt} returns None."""
        sm = SessionManager(_TEST_SECRET)
        # Manually create a signed cookie with legacy format
        legacy_payload = {"at": "access", "rt": "refresh"}
        cookie = sm._serializer.dumps(legacy_payload)
        assert sm.read_session_cookie(cookie) is None

    def test_cookie_without_uid_rejected(self):
        """Cookie without uid field returns None."""
        sm = SessionManager(_TEST_SECRET)
        bad_payload = {"role": "user", "iat": int(time.time())}
        cookie = sm._serializer.dumps(bad_payload)
        assert sm.read_session_cookie(cookie) is None
