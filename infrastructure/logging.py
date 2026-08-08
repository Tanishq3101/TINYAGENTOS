# infrastructure/logging.py

"""
Logging system for TinyAgentOS.

Provides:
- Structured logging with UTF-8 support
- Console + file output
- Windows-compatible emoji handling
"""

import logging
import sys
import os



handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
))

# Force UTF-8
handler.stream.reconfigure(encoding='utf-8')

logger = logging.getLogger("tinyagentos")
logger.setLevel(logging.DEBUG)
logger.addHandler(handler)

class UTF8StreamHandler(logging.StreamHandler):
    """
    Custom stream handler that handles UTF-8 encoding on Windows.
    
    Windows console uses cp1252 by default, which can't encode emoji.
    This handler gracefully handles encoding errors.
    """

    def emit(self, record):
        """Emit a record, handling encoding errors gracefully."""
        try:
            msg = self.format(record)
            stream = self.stream

            if sys.platform == "win32":
                try:
                    stream.write(msg + self.terminator)
                    stream.flush()
                except UnicodeEncodeError:
                    # Fallback: Remove problematic characters
                    safe_msg = msg.encode('ascii', 'ignore').decode('ascii')
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
    - File logging with UTF-8
    - Consistent formatting
    """

    logger = logging.getLogger("tinyagentos")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    os.makedirs("logs", exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    # Console Handler (UTF-8 compatible)
    console_handler = UTF8StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    # File Handler (UTF-8 encoding)
    file_handler = logging.FileHandler(
        "logs/app.log",
        encoding="utf-8"
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
def log_error(message: str, exc: Exception = None, **kwargs):
    """Log an error with optional exception details."""
    if exc:
        logger.error(f"{message}: {str(exc)}", extra=kwargs)
    else:
        logger.error(message, extra=kwargs)


def log_info(message: str, **kwargs):
    """Log an info message with context."""
    logger.info(message, extra=kwargs)


def log_debug(message: str, **kwargs):
    """Log a debug message with context."""
    logger.debug(message, extra=kwargs)


def log_warning(message: str, **kwargs):
    """Log a warning message with context."""
    logger.warning(message, extra=kwargs)