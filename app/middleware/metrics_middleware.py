"""Lightweight ASGI middleware for request metrics collection.

Records method, path, status code, and duration for every HTTP request.
Skips static file paths. All data stored in-memory only.
"""

from __future__ import annotations

import time

from app.utils.request_metrics import request_metrics


class MetricsMiddleware:
    """ASGI middleware that records request metrics with minimal overhead."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")

        # Skip static files
        if path.startswith("/static/"):
            await self.app(scope, receive, send)
            return

        method: str = scope.get("method", "GET")
        start = time.monotonic()
        status_code = 200

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            status_code = 500
            raise
        finally:
            try:
                duration_ms = (time.monotonic() - start) * 1000
                # Try to get route path template from FastAPI
                route = scope.get("route")
                if route and hasattr(route, "path"):
                    path = route.path
                request_metrics.record(method, path, status_code, duration_ms)
            except Exception:
                pass  # Never break the request pipeline
