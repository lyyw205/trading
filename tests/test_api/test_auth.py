"""Auth endpoint tests.

Unit tests verify module imports and route registration.
Integration tests (requiring full app + DB) will be added in Phase 2.
"""
from __future__ import annotations

import pytest


@pytest.mark.unit
class TestAuthRouterRegistration:
    """Verify auth router is properly configured."""

    def test_auth_router_imports(self):
        """Auth router module imports without errors."""
        from app.api.auth import router
        assert router.prefix == "/api/auth"

    def test_auth_router_has_login_route(self):
        """Login route is registered."""
        from app.api.auth import router
        paths = [route.path for route in router.routes]
        assert any("login" in p for p in paths)

    def test_auth_router_has_logout_route(self):
        """Logout route is registered."""
        from app.api.auth import router
        paths = [route.path for route in router.routes]
        assert any("logout" in p for p in paths)

    def test_auth_router_has_me_route(self):
        """User info route is registered."""
        from app.api.auth import router
        paths = [route.path for route in router.routes]
        assert any("/me" in p for p in paths)

    def test_login_schema_requires_email_and_password(self):
        """LoginRequest schema requires email and password fields."""
        import pydantic

        from app.schemas.auth import LoginRequest
        with pytest.raises(pydantic.ValidationError):
            LoginRequest()  # Missing required fields

    def test_login_schema_accepts_valid_data(self):
        """LoginRequest schema accepts valid email and password."""
        from app.schemas.auth import LoginRequest
        req = LoginRequest(email="user@example.com", password="test12345")
        assert req.email == "user@example.com"
        assert req.password == "test12345"
