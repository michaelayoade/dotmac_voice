import json
import logging
import logging.config
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
actor_id_context: ContextVar[str | None] = ContextVar("actor_id", default=None)


def set_log_context(request_id: str | None = None, actor_id: str | None = None) -> None:
    request_id_context.set(request_id)
    actor_id_context.set(actor_id)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "request_id",
            "actor_id",
            "path",
            "method",
            "status",
            "duration_ms",
        ):
            value = getattr(record, key, None)
            if value is None and key == "request_id":
                value = request_id_context.get()
            if value is None and key == "actor_id":
                value = actor_id_context.get()
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": JsonLogFormatter,
            }
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "json",
            }
        },
        "root": {"handlers": ["default"], "level": "INFO"},
    }
    logging.config.dictConfig(logging_config)
