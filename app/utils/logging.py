import logging
import json
import contextvars

current_account_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "account_id", default="system"
)


class StructuredFormatter(logging.Formatter):
    """JSON 구조화 로깅. account_id를 모든 로그에 자동 포함."""

    def format(self, record):
        log_data = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "account_id": current_account_id.get(),
            "msg": record.getMessage(),
            "module": record.module,
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """애플리케이션 로깅 설정."""
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
