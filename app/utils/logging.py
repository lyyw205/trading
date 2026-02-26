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

    # 감사 로그 전용 핸들러 (JSON 문자열을 그대로 출력)
    audit = logging.getLogger("audit")
    audit.propagate = False
    audit_handler = logging.StreamHandler()
    audit_handler.setFormatter(logging.Formatter("%(message)s"))
    audit.addHandler(audit_handler)
    audit.setLevel(logging.INFO)


def audit_log(event: str, user_id: str, account_id: str | None = None, **kwargs) -> None:
    """보안 감사 로그 기록."""
    from datetime import datetime, timezone
    data = {
        "event": event,
        "user_id": user_id,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if account_id:
        data["account_id"] = account_id
    data.update(kwargs)
    logging.getLogger("audit").info(json.dumps(data, ensure_ascii=False))
