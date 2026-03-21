from __future__ import annotations

from app.config import get_settings


class SecurityHeadersMiddleware:
    """Add security headers to all responses."""

    _HEADERS = [
        (b"x-frame-options", b"DENY"),
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        # NOTE: unsafe-inline is required for script-src and style-src because 14+ templates
        # use inline <script>, onclick handlers, and 198 inline style attributes.
        # Removing it requires a nonce-based CSP system across all templates (SEC-H2 — deferred).
        (
            b"content-security-policy",
            b"default-src 'self'; "
            b"script-src 'self' 'unsafe-inline' https://unpkg.com; "
            b"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
            b"font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
            b"img-src 'self' data:; "
            b"connect-src 'self'; "
            b"frame-ancestors 'none'",
        ),
    ]

    def __init__(self, app):
        self.app = app
        self._all_headers = list(self._HEADERS)
        settings = get_settings()
        if not settings.debug:
            self._all_headers.append((b"strict-transport-security", b"max-age=63072000; includeSubDomains"))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(self._all_headers)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
