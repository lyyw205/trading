"""Request ID middleware — injects unique request_id into every API request's context."""
from uuid import uuid4

from app.utils.logging import current_request_id


class RequestIdMiddleware:
    """ASGI middleware that generates/propagates X-Request-ID header."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract from incoming header or generate new
        headers = dict(scope.get("headers", []))
        request_id = headers.get(b"x-request-id", b"").decode() or uuid4().hex[:12]

        token = current_request_id.set(request_id)

        # Inject X-Request-ID into response headers
        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            current_request_id.reset(token)
