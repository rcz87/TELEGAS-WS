# Logger - Centralized Logging System
# Singleton pattern + JSON structured logging for file output

"""
Logger Module

Responsibilities:
- Setup centralized logging with singleton pattern
- Console: human-readable plain text
- File: JSON structured logging (for observability tools)
- Prevent duplicate handler registration
"""

import json
import logging
import sys
import atexit
import traceback
from datetime import datetime, timezone
from pathlib import Path
from logging.handlers import RotatingFileHandler

# Global registry to track configured loggers
_configured_loggers = {}


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured log output (file handler)."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add extra fields if provided via logger.info("msg", extra={"symbol": "BTC"})
        reserved = {
            "name", "msg", "args", "created", "relativeCreated",
            "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "filename", "module", "pathname", "thread", "threadName",
            "process", "processName", "levelname", "levelno", "message",
            "msecs", "taskName",
        }
        for key, value in record.__dict__.items():
            if key not in reserved and not key.startswith("_"):
                log_entry[key] = value

        # Add exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info),
            }

        return json.dumps(log_entry, default=str)


def setup_logger(name: str = "teleglas", level: str = "INFO", log_file: str = None):
    """
    Setup logger with console (plain text) and file (JSON) handlers.

    Returns existing logger if already configured (singleton pattern).

    Args:
        name: Logger name
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional log file path (JSON structured output)

    Returns:
        Configured logger instance
    """

    # Return existing logger if already configured
    if name in _configured_loggers:
        return _configured_loggers[name]

    logger = logging.getLogger(name)

    # Check if handlers already exist
    if logger.handlers:
        _configured_loggers[name] = logger
        return logger

    logger.setLevel(getattr(logging, level.upper()))
    logger.propagate = False

    # Console handler — plain text (human-readable)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)

    # File handler — JSON structured (for observability tools)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10485760,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setFormatter(JSONFormatter())
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)

    # Cleanup on exit
    def cleanup_handlers():
        for handler in logger.handlers[:]:
            try:
                handler.close()
                logger.removeHandler(handler)
            except Exception:
                pass

    atexit.register(cleanup_handlers)

    _configured_loggers[name] = logger
    return logger


def get_logger(name: str = "teleglas"):
    """Get existing logger or create new one."""
    if name in _configured_loggers:
        return _configured_loggers[name]
    return setup_logger(name)


# Create default logger (only once)
default_logger = setup_logger("teleglas", "INFO", "logs/teleglas.log")
