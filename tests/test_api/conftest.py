"""API test fixtures — authenticated clients."""

from __future__ import annotations

import uuid

import pytest_asyncio

from app.config import GlobalConfig
from app.models.user import UserProfile
from app.services.session_manager import SessionManager


@pytest_asyncio.fixture
async def authenticated_client(app_client, db_session):
    """
    httpx AsyncClient with a valid user session cookie.

    Creates a real user in the DB so auth middleware's DB validation passes.
    """
    settings = GlobalConfig()
    session_mgr = SessionManager(settings.session_secret_key_list)

    user = UserProfile(
        id=uuid.uuid4(),
        email="testuser@example.com",
        role="user",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    cookie_value = session_mgr.create_session_cookie(
        user_id=str(user.id),
        email=user.email,
        role=user.role,
    )
    app_client.cookies.set(session_mgr.cookie_name, cookie_value)
    yield app_client
    app_client.cookies.clear()


@pytest_asyncio.fixture
async def admin_client(app_client, db_session):
    """
    httpx AsyncClient with an admin session cookie.

    Creates a real admin user in the DB so auth middleware's DB validation passes.
    """
    settings = GlobalConfig()
    session_mgr = SessionManager(settings.session_secret_key_list)

    user = UserProfile(
        id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    cookie_value = session_mgr.create_session_cookie(
        user_id=str(user.id),
        email=user.email,
        role=user.role,
    )
    app_client.cookies.set(session_mgr.cookie_name, cookie_value)
    yield app_client
    app_client.cookies.clear()
