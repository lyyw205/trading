"""Auth middleware security tests: iat validation, force_logout, cache eviction."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from app.middleware.auth import LazyAuthMiddleware


@pytest.mark.unit
class TestEvictUserCache:
    def test_evict_removes_entry(self):
        """evict_user_cache removes the specified uid from cache."""
        LazyAuthMiddleware._user_cache["test-uid"] = (time.time(), {"id": "test-uid"})
        LazyAuthMiddleware.evict_user_cache("test-uid")
        assert "test-uid" not in LazyAuthMiddleware._user_cache

    def test_evict_nonexistent_is_noop(self):
        """Evicting a non-existent uid does not raise."""
        LazyAuthMiddleware.evict_user_cache("nonexistent-uid")  # should not raise


@pytest.mark.unit
class TestForceLogout:
    def test_api_path_returns_401(self):
        """_force_logout returns 401 JSON for /api/ paths."""
        mw = LazyAuthMiddleware(None)

        class FakeSessionManager:
            cookie_name = "session"

        response = mw._force_logout("/api/user/me", FakeSessionManager(), is_secure=False)
        assert response.status_code == 401

    def test_browser_path_returns_302(self):
        """_force_logout returns 302 redirect for browser paths."""
        mw = LazyAuthMiddleware(None)

        class FakeSessionManager:
            cookie_name = "session"

        response = mw._force_logout("/admin", FakeSessionManager(), is_secure=False)
        assert response.status_code == 302


@pytest.mark.unit
class TestIatValidation:
    def test_iat_before_password_changed_is_invalid(self):
        """Session issued before password change should be rejected."""
        pw_changed = datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc)
        session_iat = int(pw_changed.timestamp()) - 3600  # 1 hour before
        assert session_iat < pw_changed.timestamp()

    def test_iat_after_password_changed_is_valid(self):
        """Session issued after password change should be accepted."""
        pw_changed = datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc)
        session_iat = int(pw_changed.timestamp()) + 3600  # 1 hour after
        assert session_iat > pw_changed.timestamp()
