"""Structured logging configuration for ES Agent Management.

Replaces all print() with structured JSON logging that includes
correlation IDs, level, module name, and structured fields.
"""

import json
import logging
import sys
import threading
import uuid
from datetime import datetime, timezone

# Correlation ID per request
_request_id = threading.local()


def get_request_id() -> str:
    """Get the current request's correlation ID, creating one if none exists."""
    if not hasattr(_request_id, 'id') or _request_id.id is None:
        _request_id.id = str(uuid.uuid4())
    return _request_id.id


def set_request_id(request_id: str | None = None) -> None:
    """Set the correlation ID for the current request."""
    _request_id.id = request_id or str(uuid.uuid4())


def clear_request_id() -> None:
    """Clear the correlation ID for the current request."""
    _request_id.id = None


class JSONFormatter(logging.Formatter):
    """Format log records as JSON with standard fields."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "time": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)
        return json.dumps(log_entry)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logger with JSON formatting.

    Args:
        level: The logging level for the root logger (default: logging.INFO).
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = []
    root.addHandler(handler)

    # Quiet noisy libs
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.ERROR)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with JSON formatting.

    Args:
        name: The logger name (typically __name__).

    Returns:
        A configured logger instance.
    """
    return logging.getLogger(name)
