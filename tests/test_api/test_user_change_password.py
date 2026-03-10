"""User change-password endpoint tests.

Unit tests verify module imports, route registration, and schema validation.
"""

from __future__ import annotations

import pydantic
import pytest


@pytest.mark.unit
class TestUserRouterRegistration:
    """Verify user router is properly configured."""

    def test_user_router_imports(self):
        """User router module imports without errors."""
        from app.api.user import router

        assert router.prefix == "/api/user"

    def test_user_router_has_change_password_route(self):
        """Change-password route is registered."""
        from app.api.user import router

        paths = [route.path for route in router.routes]
        assert any("change-password" in p for p in paths)


@pytest.mark.unit
class TestChangePasswordSchema:
    """Verify ChangePasswordRequest schema validation."""

    def test_requires_both_fields(self):
        """Both current_password and new_password are required."""
        from app.schemas.auth import ChangePasswordRequest

        with pytest.raises(pydantic.ValidationError):
            ChangePasswordRequest()

    def test_rejects_short_new_password(self):
        """New password under 8 chars is rejected."""
        from app.schemas.auth import ChangePasswordRequest

        with pytest.raises(pydantic.ValidationError, match="8자"):
            ChangePasswordRequest(current_password="oldpass123", new_password="short")

    def test_accepts_valid_data(self):
        """Valid current + new password accepted."""
        from app.schemas.auth import ChangePasswordRequest

        req = ChangePasswordRequest(current_password="oldpass123", new_password="newpass123")
        assert req.current_password == "oldpass123"
        assert req.new_password == "newpass123"

    def test_current_password_no_min_length(self):
        """Current password has no minimum length (it's validated against DB, not schema)."""
        from app.schemas.auth import ChangePasswordRequest

        # current_password can be any string, validation happens at authenticate()
        req = ChangePasswordRequest(current_password="short", new_password="newpass123")
        assert req.current_password == "short"
