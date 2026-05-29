"""Rotating-file logging for the widget.

The collector historically swallowed OAuth/refresh failures silently, so a
revoked refresh token could stall usage recording for days with no trace. This
module sets up a single rotating log file (``<claude_dir>/claude-usage.log``)
that the collector and widget write warnings/errors to.

Logging must never crash the app, so setup is best-effort: any failure to open
the log file is ignored and the logger falls back to Python's last-resort
stderr handler.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

LOGGER_NAME = "claude_usage"
LOG_FILENAME = "claude-usage.log"


def setup_logging(claude_dir: str, level: int = logging.INFO) -> logging.Logger:
    """Attach a rotating file handler to the package logger (idempotent).

    Safe to call more than once: a second call when a file handler already
    exists is a no-op, so we never attach duplicate handlers. Never raises --
    a logging-setup failure must not stop the widget from running.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    if any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        return logger
    try:
        os.makedirs(claude_dir, exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(claude_dir, LOG_FILENAME),
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    except OSError:
        pass  # best-effort: fall back to last-resort stderr handler
    return logger


def get_logger() -> logging.Logger:
    """Return the package logger.

    Handlers are attached lazily by :func:`setup_logging`; if it was never
    called (e.g. a one-off ``--once`` CLI run), warnings still surface via
    Python's last-resort stderr handler.
    """
    return logging.getLogger(LOGGER_NAME)
