"""API test fixtures — authenticated clients."""
from __future__ import annotations

import pytest_asyncio


@pytest_asyncio.fixture
async def authenticated_client(app_client):
    """
    httpx AsyncClient with a valid user session cookie.

    Injects a signed session cookie directly via SessionManager so that
    the LazyAuthMiddleware considers the request authenticated.  No DB
    write is needed because session validation is stateless (HMAC-signed
    cookie).
    """
    # Build a minimal session manager with the same secret the app uses
    # (falls back to the test default when SESSION_SECRET_KEY is unset)
    from app.config import GlobalConfig
    from app.services.session_manager import SessionManager

    settings = GlobalConfig()
    session_mgr = SessionManager(settings.session_secret_key)

    import uuid

    cookie_value = session_mgr.create_session_cookie(
        user_id=str(uuid.uuid4()),
        email="testuser@example.com",
        role="user",
    )
    app_client.cookies.set(session_mgr.cookie_name, cookie_value)
    yield app_client
    app_client.cookies.clear()


@pytest_asyncio.fixture
async def admin_client(app_client):
    """
    httpx AsyncClient with an admin session cookie.

    Same mechanism as ``authenticated_client`` but role='admin'.
    """
    import uuid

    from app.config import GlobalConfig
    from app.services.session_manager import SessionManager

    settings = GlobalConfig()
    session_mgr = SessionManager(settings.session_secret_key)
    cookie_value = session_mgr.create_session_cookie(
        user_id=str(uuid.uuid4()),
        email="admin@example.com",
        role="admin",
    )
    app_client.cookies.set(session_mgr.cookie_name, cookie_value)
    yield app_client
    app_client.cookies.clear()
