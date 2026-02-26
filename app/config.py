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

    @property
    def encryption_key_list(self) -> list[str]:
        return [k.strip() for k in self.encryption_keys.split(",") if k.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
