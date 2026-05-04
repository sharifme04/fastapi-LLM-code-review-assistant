"""Structured JSON logging configuration.

Uses python-json-logger to emit JSON logs with request_id, duration_ms, and level.
Never use print() — always use the configured logger.
"""

import logging
import sys
import uuid
from contextvars import ContextVar

from pythonjsonlogger import jsonlogger

# Context variable for request-scoped request_id
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter that injects request_id from context."""

    def add_fields(
        self,
        log_record: dict,
        record: logging.LogRecord,
        message_dict: dict,
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["level"] = record.levelname.upper()
        log_record["logger"] = record.name
        log_record["timestamp"] = self.formatTime(record)

        rid = request_id_ctx.get("")
        if rid:
            log_record["request_id"] = rid


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure structured JSON logging for the application.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).

    Returns:
        Configured root logger.
    """
    log = logging.getLogger("code_review_assistant")
    log.setLevel(getattr(logging, level.upper(), logging.INFO))

    log.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    formatter = CustomJsonFormatter(
        fmt="%(timestamp)s %(level)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)
    log.addHandler(handler)
    log.propagate = False

    return log


def generate_request_id() -> str:
    """Generate a unique request ID."""
    return str(uuid.uuid4())[:8]


logger = setup_logging()
