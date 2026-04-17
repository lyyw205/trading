from __future__ import annotations

import collections
import json
import logging
import re
from datetime import UTC, datetime

from app.utils.context import current_account_id, current_cycle_id, current_request_id  # noqa: F401


class LogBuffer:
    """Thread-safe in-memory ring buffer for structured log entries."""

    def __init__(self, maxsize: int = 2000) -> None:
        self._buf: collections.deque[dict] = collections.deque(maxlen=maxsize)

    def append(self, entry: dict) -> None:
        self._buf.append(entry)

    def get_logs(
        self,
        account_id: str | None = None,
        level: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        level_up = level.upper() if level else None
        results = [
            e
            for e in self._buf
            if (account_id is None or e.get("account_id") == account_id)
            and (level_up is None or e.get("level") == level_up)
        ]
        return results[-limit:]


log_buffer = LogBuffer()

# Module-level reference for lifespan access (type: PersistLogHandler | None, set by setup_logging)
persist_handler = None


_SENSITIVE_RE = re.compile(
    r"(api[_-]?key|api[_-]?secret|password|secret[_-]?key|token|authorization|encryption[_-]?key)"
    r"[\s]*[=:]\s*['\"]?([^\s'\",:}{]{4,})",
    re.IGNORECASE,
)


def _sanitize_msg(msg: str) -> str:
    """Mask sensitive values (password=xxx → password=***) in log messages."""
    return _SENSITIVE_RE.sub(lambda m: f"{m.group(1)}=***", msg)


class StructuredFormatter(logging.Formatter):
    """JSON structured logging with correlation IDs and sensitive-data masking."""

    def format(self, record):
        log_data = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%d %H:%M:%S+00:00"),
            "level": record.levelname,
            "account_id": current_account_id.get(),
            "request_id": current_request_id.get(),
            "cycle_id": current_cycle_id.get(),
            "msg": _sanitize_msg(record.getMessage()),
            "module": record.module,
        }
        # Optional duration field
        duration_ms = getattr(record, "duration_ms", None)
        if duration_ms is not None:
            log_data["duration_ms"] = duration_ms
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        log_buffer.append(log_data)
        return json.dumps(log_data, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """애플리케이션 로깅 설정."""
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 3rd party 라이브러리 노이즈 억제
    for name in (
        "sqlalchemy.engine",  # SQL echo (sql_echo=False일 때도 남는 잔여 로그)
        "sqlalchemy.pool",  # 커넥션 풀 이벤트
        "asyncpg",  # 드라이버 레벨 로그
        "binance",  # python-binance 내부 로그
        "websockets",  # WebSocket 프레임 로그
        "httpx",  # HTTP 클라이언트
        "httpcore",  # httpx 하위 레이어
        "uvicorn.access",  # 요청별 접근 로그 (프록시 뒤에서 불필요)
        "watchfiles",  # --reload 파일 감지
    ):
        logging.getLogger(name).setLevel(logging.WARNING)

    # 감사 로그 전용 핸들러 (JSON 문자열을 그대로 출력)
    # stdout 핸들러는 유지하되, propagate=True로 PersistLogHandler에도 전달하여
    # 감사 이벤트가 DB에 보존되도록 한다. (Option A2)
    # TODO: 감사 쿼리가 복잡해지면 전용 AuditLog 모델+테이블로 마이그레이션 검토
    audit = logging.getLogger("audit")
    audit.propagate = True
    audit.handlers.clear()
    audit_handler = logging.StreamHandler()
    audit_handler.setFormatter(logging.Formatter("%(message)s"))
    audit.addHandler(audit_handler)
    audit.setLevel(logging.INFO)

    # DB persistence handler for ERROR+ logs
    from app.utils.log_persist_handler import PersistLogHandler  # noqa: PLC0415

    global persist_handler
    persist_handler = PersistLogHandler()
    root.addHandler(persist_handler)


def audit_log(event: str, user_id: str, account_id: str | None = None, **kwargs) -> None:
    """보안 감사 로그 기록."""
    data = {
        "event": event,
        "user_id": user_id,
        "ts": datetime.now(UTC).isoformat(),
    }
    if account_id:
        data["account_id"] = account_id
    data.update(kwargs)
    logging.getLogger("audit").info(json.dumps(data, ensure_ascii=False))
