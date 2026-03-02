from pydantic import model_validator
from pydantic_settings import BaseSettings


class GlobalConfig(BaseSettings):
    # Database (트레이딩 엔진용 직접 연결)
    database_url: str = ""

    # Initial Admin Bootstrap (최초 실행 시에만 사용)
    initial_admin_email: str = ""
    initial_admin_password: str = ""

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
        """프로덕션 환경에서 필수 시크릿이 비어있으면 시작 차단."""
        if self.environment == "production":
            if not self.session_secret_key:
                raise ValueError("SESSION_SECRET_KEY must be set in production")
            if not self.csrf_secret:
                raise ValueError("CSRF_SECRET must be set in production")
            if not self.encryption_keys:
                raise ValueError("ENCRYPTION_KEYS must be set in production")
        return self

    @property
    def encryption_key_list(self) -> list[str]:
        return [k.strip() for k in self.encryption_keys.split(",") if k.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
