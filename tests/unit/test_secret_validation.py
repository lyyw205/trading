"""Secret validation and session key rotation tests."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet


@pytest.mark.unit
class TestSessionSecretKeyListProperty:
    """Verify config property splits correctly."""

    def test_single_key(self):
        from app.config import GlobalConfig

        cfg = GlobalConfig(session_secret_key="single-key")
        assert cfg.session_secret_key_list == ["single-key"]

    def test_multi_key(self):
        from app.config import GlobalConfig

        cfg = GlobalConfig(session_secret_key="new-key,old-key,oldest-key")
        # Reversed: itsdangerous signs with last, so newest must be last
        assert cfg.session_secret_key_list == ["oldest-key", "old-key", "new-key"]

    def test_strips_whitespace(self):
        from app.config import GlobalConfig

        cfg = GlobalConfig(session_secret_key=" a , b , c ")
        assert cfg.session_secret_key_list == ["c", "b", "a"]


@pytest.mark.unit
class TestSessionManagerMultiKey:
    """Verify SessionManager multi-key rotation behavior."""

    def test_single_key_string(self):
        """Single string key works (backward compat)."""
        from app.services.session_manager import SessionManager

        mgr = SessionManager("test-secret-key")
        cookie = mgr.create_session_cookie("uid1", "test@test.com", "user")
        data = mgr.read_session_cookie(cookie)
        assert data["uid"] == "uid1"

    def test_multi_key_rotation(self):
        """Token signed with old key is readable by [new, old] manager."""
        from app.services.session_manager import SessionManager

        old_mgr = SessionManager("old-key")
        cookie = old_mgr.create_session_cookie("uid1", "test@test.com", "user")

        # New manager with both keys can read old token
        new_mgr = SessionManager(["new-key", "old-key"])
        data = new_mgr.read_session_cookie(cookie)
        assert data is not None
        assert data["uid"] == "uid1"

    def test_new_key_signing(self):
        """Multi-key manager can read its own tokens."""
        from app.services.session_manager import SessionManager

        multi_mgr = SessionManager(["new-key", "old-key"])
        cookie = multi_mgr.create_session_cookie("uid1", "test@test.com", "user")

        # Multi-key manager can read its own tokens
        data = multi_mgr.read_session_cookie(cookie)
        assert data is not None
        assert data["uid"] == "uid1"

        # Completely unrelated key cannot read it
        unrelated_mgr = SessionManager(["totally-different-key"])
        data = unrelated_mgr.read_session_cookie(cookie)
        assert data is None

    def test_signing_uses_newest_key(self):
        """Multi-key manager signs with the newest (first in env) key."""
        from app.config import GlobalConfig
        from app.services.session_manager import SessionManager

        cfg = GlobalConfig(session_secret_key="new-key,old-key")
        mgr = SessionManager(cfg.session_secret_key_list)
        cookie = mgr.create_session_cookie("uid1", "test@test.com", "user")

        # new-key alone can read it (it was used for signing)
        new_only = SessionManager("new-key")
        assert new_only.read_session_cookie(cookie) is not None

    def test_single_key_list(self):
        """Single-element list works same as string."""
        from app.services.session_manager import SessionManager

        str_mgr = SessionManager("the-key")
        list_mgr = SessionManager(["the-key"])

        cookie = str_mgr.create_session_cookie("uid1", "test@test.com", "user")
        data = list_mgr.read_session_cookie(cookie)
        assert data is not None
        assert data["uid"] == "uid1"


@pytest.mark.unit
class TestProductionSecretStrength:
    """Verify production secret strength validation."""

    def test_production_rejects_short_session_key(self):
        from app.config import GlobalConfig

        valid_fernet = Fernet.generate_key().decode()
        with pytest.raises(ValueError, match="32 characters"):
            GlobalConfig(
                environment="production",
                database_url="postgresql+asyncpg://x:x@localhost/db",
                session_secret_key="short",
                csrf_secret="a" * 32,
                encryption_keys=valid_fernet,
            )

    def test_production_rejects_short_csrf_secret(self):
        from app.config import GlobalConfig

        valid_fernet = Fernet.generate_key().decode()
        with pytest.raises(ValueError, match="CSRF_SECRET"):
            GlobalConfig(
                environment="production",
                database_url="postgresql+asyncpg://x:x@localhost/db",
                session_secret_key="a" * 32,
                csrf_secret="short",
                encryption_keys=valid_fernet,
            )

    def test_production_rejects_invalid_fernet_key(self):
        from app.config import GlobalConfig

        with pytest.raises(ValueError, match="invalid Fernet key"):
            GlobalConfig(
                environment="production",
                database_url="postgresql+asyncpg://x:x@localhost/db",
                session_secret_key="a" * 32,
                csrf_secret="b" * 32,
                encryption_keys="not-a-valid-fernet-key",
            )

    def test_production_accepts_strong_secrets(self):
        from app.config import GlobalConfig

        valid_fernet = Fernet.generate_key().decode()
        cfg = GlobalConfig(
            environment="production",
            database_url="postgresql+asyncpg://x:x@localhost/db",
            session_secret_key="a" * 32,
            csrf_secret="b" * 32,
            encryption_keys=valid_fernet,
        )
        assert cfg.environment == "production"

    def test_development_ignores_strength(self):
        """Dev environment auto-generates secrets without error."""
        from app.config import GlobalConfig

        cfg = GlobalConfig(environment="development")
        # Auto-generated keys should be strong enough
        assert len(cfg.session_secret_key) >= 32
        assert len(cfg.csrf_secret) >= 32
        assert len(cfg.encryption_keys) > 0
