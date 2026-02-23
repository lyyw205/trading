from __future__ import annotations

# starlette-csrf middleware configuration
# Usage in main.py:
#   from starlette_csrf import CSRFMiddleware
#   app.add_middleware(CSRFMiddleware, secret=settings.csrf_secret)
#
# This protects SSR POST endpoints (/dashboard/*/tune etc.)
# API endpoints using Bearer tokens are exempt from CSRF

CSRF_EXEMPT_PATHS = [
    "/api/auth/callback",
    "/health",
]
