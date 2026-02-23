from __future__ import annotations
import logging
from supabase import create_client, Client as SupabaseClient

logger = logging.getLogger(__name__)


class AuthService:
    """Supabase Auth 연동 서비스"""

    def __init__(self, supabase_url: str, supabase_anon_key: str, supabase_service_key: str = ""):
        self._url = supabase_url
        self._anon_key = supabase_anon_key
        self._service_key = supabase_service_key
        self._anon_client: SupabaseClient | None = None
        self._service_client: SupabaseClient | None = None

    def _get_anon_client(self) -> SupabaseClient:
        if self._anon_client is None:
            self._anon_client = create_client(self._url, self._anon_key)
        return self._anon_client

    def _get_service_client(self) -> SupabaseClient:
        if self._service_client is None:
            self._service_client = create_client(self._url, self._service_key)
        return self._service_client

    def get_google_oauth_url(self, redirect_url: str) -> str:
        """Get Google OAuth sign-in URL"""
        client = self._get_anon_client()
        resp = client.auth.sign_in_with_oauth({
            "provider": "google",
            "options": {"redirect_to": redirect_url},
        })
        return resp.url

    async def exchange_code_for_session(self, code: str) -> dict | None:
        """Exchange OAuth code for session tokens"""
        try:
            client = self._get_anon_client()
            resp = client.auth.exchange_code_for_session({"auth_code": code})
            if resp and resp.session:
                return {
                    "access_token": resp.session.access_token,
                    "refresh_token": resp.session.refresh_token,
                    "user": {
                        "id": str(resp.user.id),
                        "email": resp.user.email,
                    },
                }
        except Exception as e:
            logger.error(f"Code exchange failed: {e}")
        return None

    async def get_user_from_token(self, access_token: str) -> dict | None:
        """Validate access token and return user info"""
        try:
            client = self._get_anon_client()
            resp = client.auth.get_user(access_token)
            if resp and resp.user:
                return {
                    "id": str(resp.user.id),
                    "email": resp.user.email,
                }
        except Exception as e:
            logger.warning(f"Token validation failed: {e}")
        return None

    async def refresh_session(self, refresh_token: str) -> dict | None:
        """Refresh expired access token"""
        try:
            client = self._get_anon_client()
            resp = client.auth.refresh_session(refresh_token)
            if resp and resp.session:
                return {
                    "access_token": resp.session.access_token,
                    "refresh_token": resp.session.refresh_token,
                }
        except Exception as e:
            logger.warning(f"Session refresh failed: {e}")
        return None

    async def ensure_user_profile(self, user_id: str, email: str) -> None:
        """Ensure user_profiles row exists (upsert via service role)"""
        try:
            client = self._get_service_client()
            client.table("user_profiles").upsert({
                "id": user_id,
                "email": email,
            }).execute()
        except Exception as e:
            logger.warning(f"User profile upsert failed: {e}")

    async def get_user_role(self, user_id: str) -> str:
        """Get user role from user_profiles"""
        try:
            client = self._get_service_client()
            resp = client.table("user_profiles").select("role").eq("id", user_id).single().execute()
            if resp.data:
                return resp.data.get("role", "user")
        except Exception:
            pass
        return "user"
