"""CSP nonce security header tests."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.middleware.security_headers import SecurityHeadersMiddleware


def _make_middleware():
    """Instantiate middleware with debug=True to skip HSTS header."""
    with patch("app.middleware.security_headers.get_settings") as mock_settings:
        mock_settings.return_value.debug = True
        return SecurityHeadersMiddleware(app=None)


@pytest.mark.unit
class TestSecurityHeadersNonce:
    async def test_nonce_set_in_scope_state(self):
        """Middleware sets csp_nonce in scope state."""
        app_called = False
        captured_scope = {}

        async def mock_app(scope, receive, send):
            nonlocal app_called, captured_scope
            app_called = True
            captured_scope = scope

        with patch("app.middleware.security_headers.get_settings") as mock_settings:
            mock_settings.return_value.debug = True
            middleware = SecurityHeadersMiddleware(mock_app)

        scope = {"type": "http", "state": {}}

        async def mock_receive():
            return {"type": "http.request", "body": b""}

        async def mock_send(message):
            pass

        await middleware(scope, mock_receive, mock_send)
        assert app_called
        assert "csp_nonce" in captured_scope.get("state", {})

    async def test_csp_header_contains_nonce(self):
        """Response CSP header contains the generated nonce."""
        captured_nonce = None

        async def mock_app(scope, receive, send):
            nonlocal captured_nonce
            captured_nonce = scope["state"]["csp_nonce"]
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        with patch("app.middleware.security_headers.get_settings") as mock_settings:
            mock_settings.return_value.debug = True
            middleware = SecurityHeadersMiddleware(mock_app)

        scope = {"type": "http", "state": {}}
        sent_messages = []

        async def mock_send(message):
            sent_messages.append(message)

        async def mock_receive():
            return {"type": "http.request"}

        await middleware(scope, mock_receive, mock_send)

        start_msg = sent_messages[0]
        headers = dict(start_msg["headers"])
        csp = headers.get(b"content-security-policy", b"").decode()
        assert f"nonce-{captured_nonce}" in csp
        assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]

    async def test_non_http_passthrough(self):
        """Non-HTTP scopes pass through without modification."""
        app_called = False

        async def mock_app(scope, receive, send):
            nonlocal app_called
            app_called = True

        with patch("app.middleware.security_headers.get_settings") as mock_settings:
            mock_settings.return_value.debug = True
            middleware = SecurityHeadersMiddleware(mock_app)

        await middleware({"type": "websocket"}, None, None)
        assert app_called
