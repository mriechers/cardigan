"""
Cardigan - Structured JSON Logging

Provides JSON-formatted logging for consistent API log output.
Logs to both console and rotating log files for stability monitoring.

Log files:
- logs/worker.log - Main worker events
- logs/api.log - API request/response events
- OUTPUT/{project}/job.log - Per-job processing logs
"""

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

# Default log directory
LOGS_DIR = Path(os.getenv("LOGS_DIR", "logs"))


class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs logs in JSON format."""

    def format(self, record: logging.LogRecord) -> str:
        """
        Format a log record as JSON.

        Args:
            record: The log record to format

        Returns:
            JSON string with timestamp, level, logger, message, and extra fields
        """
        log_data: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "extra": {},
        }

        # Include any extra context passed via logger.info("msg", extra={...})
        standard_attrs = {
            "name",
            "msg",
            "args",
            "created",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "message",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "thread",
            "threadName",
            "exc_info",
            "exc_text",
            "stack_info",
            "getMessage",
            "asctime",
            "taskName",
        }

        for key, value in record.__dict__.items():
            if key not in standard_attrs:
                log_data["extra"][key] = value

        # Include exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


# Reserved LogRecord attributes — extra keys colliding with these cause KeyError.
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class SafeLogger(logging.Logger):
    """Logger subclass that renames extra keys colliding with LogRecord attributes."""

    def makeRecord(self, name, level, fn, lno, msg, args, exc_info, func=None, extra=None, sinfo=None):
        if extra:
            extra = {(f"extra_{k}" if k in _RESERVED else k): v for k, v in extra.items()}
        return super().makeRecord(name, level, fn, lno, msg, args, exc_info, func, extra, sinfo)


# Track whether logging has been set up
_logging_configured = False


def setup_logging(
    level: Optional[str] = None,
    log_file: Optional[str] = None,
    enable_console: bool = True,
) -> None:
    """
    Configure Python logging for JSON output to console and file.

    Args:
        level: Log level as string (DEBUG, INFO, WARNING, ERROR).
               If not specified, reads from LOG_LEVEL environment variable.
               Defaults to INFO if neither is provided.
        log_file: Optional log file name (e.g., "worker.log").
                  Will be created in LOGS_DIR.
        enable_console: Whether to also log to console (default: True)
    """
    global _logging_configured

    # Ensure all future loggers use SafeLogger to prevent extra-key collisions.
    logging.setLoggerClass(SafeLogger)

    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")

    level_upper = level.upper()

    numeric_level = getattr(logging, level_upper, None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {level}")

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler
    if enable_console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(JSONFormatter())
        root_logger.addHandler(console_handler)

    # File handler (rotating, max 10MB per file, keep 5 backups)
    if log_file:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOGS_DIR / log_file
        file_handler = RotatingFileHandler(
            log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"  # 10 MB
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(JSONFormatter())
        root_logger.addHandler(file_handler)

    _logging_configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Get a configured logger instance.

    Ensures logging is set up on first call.

    Args:
        name: Name for the logger (typically __name__)

    Returns:
        Configured logger instance
    """
    global _logging_configured

    if not _logging_configured:
        setup_logging()

    return logging.getLogger(name)
