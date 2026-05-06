import json
import logging
import traceback
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """
    Emits every log line as a single JSON object.
    Makes logs machine-parseable in production log aggregators.

    Output shape:
    {
        "timestamp": "2026-05-05T10:00:00.000Z",
        "level": "INFO",
        "logger": "whatsapp_integration.services.webhook_service",
        "message": "Message stored: id=...",
        "service": "whatsapp-api"
    }
    """

    SERVICE_NAME = "whatsapp-api"

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self.SERVICE_NAME,
        }

        # Include extra fields attached via logger.info(..., extra={...})
        for key, value in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno",
                "pathname", "filename", "module", "exc_info", "exc_text",
                "stack_info", "lineno", "funcName", "created", "msecs",
                "relativeCreated", "thread", "threadName", "processName",
                "process", "message",
            }:
                if not key.startswith("_"):
                    log_data[key] = value

        # Attach exception traceback if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
            log_data["traceback"] = traceback.format_exception(*record.exc_info)

        return json.dumps(log_data, default=str)