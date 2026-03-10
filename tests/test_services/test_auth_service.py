"""Integration tests for AuthService — brute-force, lockout, timing attack prevention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio

from app.services.auth_service import MAX_FAILED_ATTEMPTS, AuthService

pytestmark = pytest.mark.asyncio


@pytest.mark.integration
class TestAuthService:
    @pytest_asyncio.fixture
    async def auth(self, db_session_factory):
        return AuthService(session_factory=db_session_factory)

    @pytest_asyncio.fixture
    async def test_user(self, auth):
        return await auth.create_user("test@example.com", "password123", "user")

    async def test_authenticate_success(self, auth, test_user):
        result = await auth.authenticate("test@example.com", "password123")
        assert result is not None
        assert result["email"] == "test@example.com"
        assert result["role"] == "user"

    async def test_authenticate_wrong_password(self, auth, test_user):
        result = await auth.authenticate("test@example.com", "wrongpassword")
        assert result is None

    async def test_authenticate_nonexistent_user(self, auth):
        result = await auth.authenticate("nobody@example.com", "password123")
        assert result is None

    async def test_account_locks_after_max_failures(self, auth, test_user):
        for _ in range(MAX_FAILED_ATTEMPTS):
            await auth.authenticate("test@example.com", "wrongpassword")
        # Now even correct password should fail (locked)
        result = await auth.authenticate("test@example.com", "password123")
        assert result is None

    async def test_locked_account_unlocks_after_expiry(self, auth, test_user, db_session_factory):
        # Lock the account
        for _ in range(MAX_FAILED_ATTEMPTS):
            await auth.authenticate("test@example.com", "wrongpassword")
        # Manually set locked_until to past
        from sqlalchemy import update

        from app.models.user import UserProfile

        async with db_session_factory() as session:
            stmt = (
                update(UserProfile)
                .where(UserProfile.email == "test@example.com")
                .values(locked_until=datetime.now(UTC) - timedelta(minutes=1))
            )
            await session.execute(stmt)
            await session.commit()
        # Now should authenticate successfully
        result = await auth.authenticate("test@example.com", "password123")
        assert result is not None

    async def test_successful_login_resets_failure_count(self, auth, test_user, db_session_factory):
        # 3 failures
        for _ in range(3):
            await auth.authenticate("test@example.com", "wrongpassword")
        # 1 success
        result = await auth.authenticate("test@example.com", "password123")
        assert result is not None
        # Verify counter reset: 4 more failures should NOT lock (would need 5 from zero)
        for _ in range(4):
            await auth.authenticate("test@example.com", "wrongpassword")
        result = await auth.authenticate("test@example.com", "password123")
        assert result is not None  # Not locked because counter was reset

    async def test_inactive_user_cannot_authenticate(self, auth, test_user):
        await auth.set_user_active(test_user["id"], False)
        result = await auth.authenticate("test@example.com", "password123")
        assert result is None

    async def test_timing_attack_dummy_hash_called(self, auth):
        with patch("app.services.auth_service.bcrypt.checkpw", return_value=False) as mock_check:
            await auth.authenticate("nonexistent@example.com", "anypassword")
            mock_check.assert_called_once()
