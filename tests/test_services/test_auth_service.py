"""Integration tests for AuthService — brute-force, lockout, timing attack prevention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio

from app.services.auth_service import MAX_FAILED_ATTEMPTS, AuthService

# asyncio_mode = "auto" in pyproject.toml handles async tests globally — no module-level mark needed


@pytest.mark.integration
class TestAuthService:
    @pytest_asyncio.fixture
    async def auth(self, db_session_factory):
        return AuthService(session_factory=db_session_factory)

    @pytest_asyncio.fixture
    async def test_user(self, auth):
        return await auth.create_user("test@example.com", "Password12345", "user")

    async def test_authenticate_success(self, auth, test_user):
        result = await auth.authenticate("test@example.com", "Password12345")
        assert result is not None
        assert result["email"] == "test@example.com"
        assert result["role"] == "user"

    async def test_authenticate_wrong_password(self, auth, test_user):
        result = await auth.authenticate("test@example.com", "wrongpassword")
        assert result is None

    async def test_authenticate_nonexistent_user(self, auth):
        result = await auth.authenticate("nobody@example.com", "Password12345")
        assert result is None

    async def test_account_locks_after_max_failures(self, auth, test_user):
        for _ in range(MAX_FAILED_ATTEMPTS):
            await auth.authenticate("test@example.com", "wrongpassword")
        # Now even correct password should fail (locked)
        result = await auth.authenticate("test@example.com", "Password12345")
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
        result = await auth.authenticate("test@example.com", "Password12345")
        assert result is not None

    async def test_successful_login_resets_failure_count(self, auth, test_user, db_session_factory):
        # 3 failures
        for _ in range(3):
            await auth.authenticate("test@example.com", "wrongpassword")
        # 1 success
        result = await auth.authenticate("test@example.com", "Password12345")
        assert result is not None
        # Verify counter reset: 4 more failures should NOT lock (would need 5 from zero)
        for _ in range(4):
            await auth.authenticate("test@example.com", "wrongpassword")
        result = await auth.authenticate("test@example.com", "Password12345")
        assert result is not None  # Not locked because counter was reset

    async def test_inactive_user_cannot_authenticate(self, auth, test_user):
        await auth.set_user_active(test_user["id"], False)
        result = await auth.authenticate("test@example.com", "Password12345")
        assert result is None

    async def test_timing_attack_dummy_hash_called(self, auth):
        with patch("app.services.auth_service.bcrypt.checkpw", return_value=False) as mock_check:
            await auth.authenticate("nonexistent@example.com", "anypassword")
            mock_check.assert_called_once()

    async def test_get_user_by_id_returns_password_changed_at(self, auth, test_user):
        result = await auth.get_user_by_id(test_user["id"])
        assert result is not None
        assert "password_changed_at" in result

    async def test_create_user_sets_password_changed_at(self, auth, test_user):
        assert test_user is not None  # create_user already called via fixture
        result = await auth.get_user_by_id(test_user["id"])
        assert result["password_changed_at"] is not None

    async def test_reset_password_updates_password_changed_at(self, auth, test_user):
        before = await auth.get_user_by_id(test_user["id"])
        await auth.reset_password(test_user["id"], "NewValidPass123")
        after = await auth.get_user_by_id(test_user["id"])
        assert after["password_changed_at"] >= before["password_changed_at"]


@pytest.mark.unit
class TestValidatePassword:
    """AuthService._validate_password static method tests."""

    def test_rejects_short_password(self):
        from app.services.auth_service import AuthService

        with pytest.raises(ValueError, match="12자"):
            AuthService._validate_password("Short1Aa")

    def test_rejects_no_uppercase(self):
        from app.services.auth_service import AuthService

        with pytest.raises(ValueError, match="대문자"):
            AuthService._validate_password("alllowercase1!")

    def test_rejects_no_lowercase(self):
        from app.services.auth_service import AuthService

        with pytest.raises(ValueError, match="소문자"):
            AuthService._validate_password("ALLUPPERCASE1!")

    def test_rejects_no_digit(self):
        from app.services.auth_service import AuthService

        with pytest.raises(ValueError, match="숫자"):
            AuthService._validate_password("NoDigitsHereAB!")

    def test_rejects_over_72_bytes(self):
        from app.services.auth_service import AuthService

        long_pw = "Aa1" + "x" * 70  # > 72 bytes
        with pytest.raises(ValueError, match="72바이트"):
            AuthService._validate_password(long_pw)

    def test_accepts_valid_password(self):
        from app.services.auth_service import AuthService

        AuthService._validate_password("ValidPass123")  # should not raise
