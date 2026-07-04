"""Application-wide logging configuration.

Configures a single root logger with two handlers:

* A rotating file handler writing structured, timestamped log lines to
  ``logs/trading_bot.log`` (INFO and above).
* A console handler surfacing WARNING and above, so CLI users see
  problems without the log file being flooded onto their terminal.

Every other module in this project obtains its logger via
``logging.getLogger(__name__)`` and relies on propagation to these
handlers rather than configuring logging itself.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

LOG_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE: Final[Path] = LOG_DIR / "trading_bot.log"

LOG_FORMAT: Final[str] = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"

MAX_LOG_BYTES: Final[int] = 5 * 1024 * 1024  # 5 MB per file
BACKUP_COUNT: Final[int] = 3
CONSOLE_LOG_LEVEL: Final[int] = logging.WARNING


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure the application-wide root logger.

    Idempotent: safe to call multiple times (e.g. from tests or repeated
    CLI invocations within the same process) without registering
    duplicate handlers.

    Args:
        level: Minimum severity written to the rotating log file.

    Returns:
        The configured root logger.

    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if any(
        isinstance(handler, RotatingFileHandler) for handler in root_logger.handlers
    ):
        return root_logger

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(CONSOLE_LOG_LEVEL)
    console_handler.setFormatter(formatter)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    return root_logger
