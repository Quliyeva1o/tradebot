"""Structured logging module.

Sets up consistent log formatting for console and file loggers.
"""

import logging
import sys
from pathlib import Path


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
