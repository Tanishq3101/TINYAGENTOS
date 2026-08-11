# infrastructure/logging.py

"""
Logging system for TinyAgentOS.

Provides:
- Structured logging with UTF-8 support
- Console + file output (rotating, so it doesn't grow unbounded)
- Windows-compatible emoji handling

NOTE: earlier there was a module-level handler-creation block that ran at import
time AND a setup_logger() function with an "already configured" guard. Both
attached handlers to the same logger name, so the guard tripped on the
top-level handler and setup_logger() silently returned early — the file
handler and the UTF-8-safe handler were never actually attached. Fixed by
having exactly one place that configures the logger.
"""

import logging
import logging.handlers
import sys
import os
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    # logging.StreamHandler only became subscriptable (generic) in Python 3.11+.
    # Guarding this under TYPE_CHECKING keeps the annotation for type checkers
    # without breaking at import time on 3.10.
    _StreamHandlerBase = logging.StreamHandler[Any]
else:
    _StreamHandlerBase = logging.StreamHandler


class UTF8StreamHandler(_StreamHandlerBase):
    """
    Custom stream handler that handles UTF-8 encoding on Windows.

    Windows console uses cp1252 by default, which can't encode emoji.
    This handler gracefully handles encoding errors.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a record, handling encoding errors gracefully."""
        try:
            msg = self.format(record)
            stream = self.stream

            if sys.platform == "win32":
                try:
                    stream.write(msg + self.terminator)
                    stream.flush()
                except UnicodeEncodeError:
                    # Fallback: strip characters the console codepage can't render
                    safe_msg = msg.encode("ascii", "ignore").decode("ascii")
                    stream.write(safe_msg + self.terminator)
                    stream.flush()
            else:
                stream.write(msg + self.terminator)
                stream.flush()

        except Exception:
            self.handleError(record)


def setup_logger() -> logging.Logger:
    """
    Configures and returns the main application logger.

    Features:
    - UTF-8 compatible console output (Windows-safe)
    - Rotating file logging with UTF-8 (10MB per file, 5 backups)
    - Consistent formatting

    Safe to call more than once — returns the existing configured logger
    instead of stacking duplicate handlers.
    """

    logger = logging.getLogger("tinyagentos")

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # don't also send records up to the root logger

    os.makedirs("logs", exist_ok=True)

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    # Console handler (UTF-8 compatible)
    console_handler = UTF8StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    # Rotating file handler (UTF-8 encoding) — caps disk usage, unlike a plain
    # FileHandler which grows forever
    file_handler = logging.handlers.RotatingFileHandler(
        "logs/app.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    logger.info("Logger initialized successfully")

    return logger


# Global logger instance
logger = setup_logger()


# Utility functions
def log_error(message: str, exc: Optional[Exception] = None, **kwargs: Any) -> None:
    """Log an error with optional exception details."""
    if exc:
        logger.error(f"{message}: {str(exc)}", extra=kwargs)
    else:
        logger.error(message, extra=kwargs)


def log_info(message: str, **kwargs: Any) -> None:
    """Log an info message with context."""
    logger.info(message, extra=kwargs)


def log_debug(message: str, **kwargs: Any) -> None:
    """Log a debug message with context."""
    logger.debug(message, extra=kwargs)


def log_warning(message: str, **kwargs: Any) -> None:
    """Log a warning message with context."""
    logger.warning(message, extra=kwargs)
