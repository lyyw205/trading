from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.user import UserProfile

logger = logging.getLogger(__name__)

# Brute-force protection constants
MAX_FAILED_ATTEMPTS = 20
LOCK_DURATION_MINUTES = 15

# Pre-hashed dummy password for timing-attack prevention
_DUMMY_HASH = bcrypt.hashpw(b"dummy-timing-safe", bcrypt.gensalt(rounds=12))


class AuthService:
    """로컬 DB 기반 비밀번호 인증 서비스"""

    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory

    async def authenticate(self, email: str, password: str) -> dict | None:
        """
        이메일+비밀번호 인증.
        성공: {"id": str, "email": str, "role": str}
        실패: None
        """
        async with self._session_factory() as session:
            stmt = select(UserProfile).where(UserProfile.email == email)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if not user:
                # Timing attack prevention: dummy bcrypt comparison
                bcrypt.checkpw(password.encode("utf-8"), _DUMMY_HASH)
                return None

            # Check account lock
            now = datetime.now(UTC)
            if user.locked_until and user.locked_until > now:
                return None

            # Check active status
            if not user.is_active:
                return None

            # Check password (None = no password set yet)
            if not user.password_hash:
                return None

            password_valid = bcrypt.checkpw(
                password.encode("utf-8"),
                user.password_hash.encode("utf-8"),
            )

            if not password_valid:
                user.failed_login_count += 1
                if user.failed_login_count >= MAX_FAILED_ATTEMPTS:
                    user.locked_until = now + timedelta(minutes=LOCK_DURATION_MINUTES)
                    logger.warning("Account locked: %s (failed %d times)", email, user.failed_login_count)
                await session.commit()
                return None

            # Success: reset counters
            user.failed_login_count = 0
            user.locked_until = None
            await session.commit()

            return {
                "id": str(user.id),
                "email": user.email,
                "role": user.role,
            }

    async def get_user_by_id(self, user_id: str) -> dict | None:
        """UUID로 사용자 조회. 비활성 사용자 제외."""
        async with self._session_factory() as session:
            stmt = select(UserProfile).where(UserProfile.id == UUID(user_id))
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user or not user.is_active:
                return None
            return {
                "id": str(user.id),
                "email": user.email,
                "role": user.role,
            }

    async def create_user(self, email: str, password: str, role: str = "user") -> dict:
        """새 사용자 생성. 비밀번호 최소 8자."""
        if len(password) < 8:
            raise ValueError("비밀번호는 최소 8자 이상이어야 합니다.")
        if len(password) > 128:
            raise ValueError("비밀번호는 128자를 초과할 수 없습니다.")

        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12))

        async with self._session_factory() as session:
            # Check duplicate email
            stmt = select(UserProfile).where(UserProfile.email == email)
            result = await session.execute(stmt)
            if result.scalar_one_or_none():
                raise ValueError("이미 등록된 이메일입니다.")

            new_user = UserProfile(
                id=uuid4(),
                email=email,
                password_hash=hashed.decode("utf-8"),
                role=role,
                password_changed_at=datetime.now(UTC),
            )
            session.add(new_user)
            await session.commit()
            await session.refresh(new_user)

            return {
                "id": str(new_user.id),
                "email": new_user.email,
                "role": new_user.role,
            }

    async def reset_password(self, user_id: str, new_password: str) -> bool:
        """비밀번호 초기화. 잠금 해제 포함."""
        if len(new_password) < 8:
            raise ValueError("비밀번호는 최소 8자 이상이어야 합니다.")
        if len(new_password) > 128:
            raise ValueError("비밀번호는 128자를 초과할 수 없습니다.")

        hashed = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt(rounds=12))

        async with self._session_factory() as session:
            stmt = select(UserProfile).where(UserProfile.id == UUID(user_id))
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user:
                return False

            user.password_hash = hashed.decode("utf-8")
            user.password_changed_at = datetime.now(UTC)
            user.failed_login_count = 0
            user.locked_until = None
            await session.commit()
            return True

    async def set_user_active(self, user_id: str, active: bool) -> bool:
        """계정 활성/비활성화."""
        async with self._session_factory() as session:
            stmt = select(UserProfile).where(UserProfile.id == UUID(user_id))
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user:
                return False

            user.is_active = active
            await session.commit()
            return True
