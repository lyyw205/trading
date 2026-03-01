from __future__ import annotations

import re

# starlette-csrf middleware configuration
# Usage in main.py:
#   from starlette_csrf import CSRFMiddleware
#   app.add_middleware(CSRFMiddleware, secret=settings.csrf_secret)
#
# This protects SSR POST endpoints (/dashboard/*/tune etc.)
# API endpoints using Bearer tokens are exempt from CSRF
# Note: starlette-csrf expects compiled regex patterns for exempt_urls

CSRF_EXEMPT_PATHS = [
    re.compile(r"^/health$"),
    re.compile(r"^/api/auth/"),
    re.compile(r"^/metrics$"),
]
