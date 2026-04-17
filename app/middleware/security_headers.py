from __future__ import annotations

import secrets

from app.config import get_settings


class SecurityHeadersMiddleware:
    """Add security headers to all responses."""

    _STATIC_HEADERS = [
        (b"x-frame-options", b"DENY"),
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    ]

    def __init__(self, app):
        self.app = app
        self._all_static = list(self._STATIC_HEADERS)
        settings = get_settings()
        if not settings.debug:
            self._all_static.append((b"strict-transport-security", b"max-age=63072000; includeSubDomains"))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Generate per-request nonce and store in scope state for template access
        nonce = secrets.token_urlsafe(24)
        scope.setdefault("state", {})
        scope["state"]["csp_nonce"] = nonce

        csp = (
            f"default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}' https://unpkg.com; "
            f"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
            f"font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
            f"img-src 'self' data:; "
            f"connect-src 'self'; "
            f"frame-ancestors 'none'"
        )

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(self._all_static)
                headers.append((b"content-security-policy", csp.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
