"""Structured logging module.

Sets up consistent log formatting for console and file loggers.

setup_logger() (below) is the long-standing human-readable text logger used
throughout the codebase -- unchanged. setup_structured_logger() is additive
(Sprint 6c, T2): a separate, JSON-line logger for machine-parseable event
streams (currently: execution/event_log.py's order-fill/slippage events, and
live_signal_check.py's data-quality events), so those can be aggregated
later (e.g. "average realized slippage over N trades") without scraping
human-readable log text. Existing modules keep using setup_logger() exactly
as before -- nothing about it changed.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def setup_logger(
    name: str = "trading_framework",
    log_level: int = logging.INFO,
    log_to_file: bool = False,
    log_dir: str | Path = "logs",
) -> logging.Logger:
    """Configures and returns a logger instance.

    Args:
        name: Logger identity.
        log_level: Severity logging level limit.
        log_to_file: Whether logs are piped to disk.
        log_dir: File path location for log outputs.

    Returns:
        A logging.Logger object.
    """
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s [%(name)s] [%(filename)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_to_file:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path / f"{name}.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


class JsonFormatter(logging.Formatter):
    """Formats each log record as a single JSON line.

    A record's `msg` is expected to be a dict of structured fields (e.g.
    `logger.info({"event_type": "fill", ...})`) -- Python's stdlib logging
    accepts any object as `msg`, so this works without a third-party
    structured-logging dependency. A plain string `msg` is also supported
    (wrapped under an "event" key) so this formatter degrades gracefully if
    ever attached to a call site that logs a normal string.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Renders record as a single JSON-encoded line.

        Args:
            record: The log record to format.

        Returns:
            A JSON string: timestamp, level, logger name, and either the
            record's dict payload merged in directly, or {"event": <message>}
            for a plain-string message.
        """
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        if isinstance(record.msg, dict):
            payload.update(record.msg)
        else:
            payload["event"] = record.getMessage()
        return json.dumps(payload, default=str)


def setup_structured_logger(
    name: str = "structured_events",
    log_level: int = logging.INFO,
    log_to_file: bool = True,
    log_dir: str | Path = "logs",
) -> logging.Logger:
    """Configures and returns a JSON-line structured event logger.

    Separate from setup_logger()'s human-readable loggers -- a distinct
    logger name means a distinct, independently-parseable log stream (its
    own file when log_to_file=True), so existing modules' console/log-file
    output format is completely unaffected.

    Args:
        name: Logger identity (also the log file's basename).
        log_level: Severity logging level limit.
        log_to_file: Whether logs are piped to disk. Defaults to True (unlike
            setup_logger()'s default False) since the point of a structured
            event stream is durable, later-aggregatable history (e.g.
            "average realized slippage over N trades"), not just live
            console visibility.
        log_dir: File path location for log outputs.

    Returns:
        A logging.Logger object whose handlers use JsonFormatter.
    """
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    if logger.handlers:
        return logger

    formatter = JsonFormatter()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_to_file:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path / f"{name}.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
