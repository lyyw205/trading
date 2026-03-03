import logging
import secrets
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_config_logger = logging.getLogger(__name__)


class GlobalConfig(BaseSettings):
    # Database (트레이딩 엔진용 직접 연결)
    database_url: str = ""

    # Encryption (쉼표 구분 다중 키)
    encryption_keys: str = ""  # "key1,key2,key3"

    # Session
    session_secret_key: str = ""
    csrf_secret: str = ""

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = False
    log_level: str = "INFO"
    sql_echo: bool = False  # SQLAlchemy SQL echo (DEBUG와 독립)
    cors_origins: str = "http://localhost:3000"

    # Rate Limiting
    api_rate_limit: int = 1000
    max_accounts_per_instance: int = 20
    thread_pool_size: int = 20

    # Monitoring
    sentry_dsn: str = ""
    environment: str = "development"
    slow_query_threshold_ms: int = 200

    # Alerts (Telegram)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    alert_rate_limit_per_hour: int = 10

    @model_validator(mode="after")
    def _validate_secrets(self):
        """프로덕션: 필수 시크릿 누락 시 시작 차단. 개발: 자동 생성 + 경고."""
        if self.environment == "production":
            if not self.session_secret_key:
                raise ValueError("SESSION_SECRET_KEY must be set in production")
            if not self.csrf_secret:
                raise ValueError("CSRF_SECRET must be set in production")
            if not self.encryption_keys:
                raise ValueError("ENCRYPTION_KEYS must be set in production")
        else:
            if not self.session_secret_key:
                self.session_secret_key = secrets.token_urlsafe(32)
                _config_logger.warning("SESSION_SECRET_KEY auto-generated (non-production)")
            if not self.csrf_secret:
                self.csrf_secret = secrets.token_urlsafe(32)
                _config_logger.warning("CSRF_SECRET auto-generated (non-production)")
            if not self.encryption_keys:
                self.encryption_keys = secrets.token_urlsafe(32)
                _config_logger.warning("ENCRYPTION_KEYS auto-generated (non-production)")
        return self

    @property
    def encryption_key_list(self) -> list[str]:
        return [k.strip() for k in self.encryption_keys.split(",") if k.strip()]

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> GlobalConfig:
    """Cached singleton for GlobalConfig."""
    return GlobalConfig()
