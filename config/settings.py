"""Framework settings loader.

Parses configuration values from environment variables and environment files (.env).
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from utils.logging import setup_logger

# Load environment variables from .env file
load_dotenv()

logger = setup_logger("settings")

VALID_DUPLICATE_POLICIES = frozenset({"drop", "keep"})
VALID_MISSING_VALUE_POLICIES = frozenset({"raise", "drop", "fill"})


def _parse_mt5_login(raw: str) -> int:
    """Parses MT5_LOGIN from its raw env string, defaulting to 0 on bad input.

    This runs at class-definition (import) time via the dataclass field
    default below, so a malformed value must never raise -- doing so would
    crash every module that imports Settings (e.g. CSVDataProvider), not
    just MT5-related code.
    """
    try:
        return int(raw)
    except ValueError:
        logger.warning("MT5_LOGIN=%r is not a valid integer; defaulting to 0.", raw)
        return 0


def _parse_max_daily_loss_pct(raw: str) -> float:
    """Parses MAX_DAILY_LOSS_PCT from its raw env string, defaulting to 0.05 (5%).

    Same never-raise-at-import-time contract as _parse_mt5_login (Bug #26):
    this runs as a bare dataclass field default at class-definition time, so
    a malformed or non-positive value must degrade to a safe default instead
    of crashing every module that imports Settings.
    """
    try:
        value = float(raw)
    except ValueError:
        logger.warning("MAX_DAILY_LOSS_PCT=%r is not a valid float; defaulting to 0.05.", raw)
        return 0.05
    if value <= 0:
        logger.warning("MAX_DAILY_LOSS_PCT=%r must be positive; defaulting to 0.05.", raw)
        return 0.05
    return value


@dataclass(frozen=True)
class Settings:
    """Read-only system configurations loaded from environment settings."""

    # MetaTrader 5 execution settings
    MT5_LOGIN: int = _parse_mt5_login(os.getenv("MT5_LOGIN", "0"))
    MT5_PASSWORD: str = os.getenv("MT5_PASSWORD", "")
    MT5_SERVER: str = os.getenv("MT5_SERVER", "MetaQuotes-Demo")
    MT5_PATH: str = os.getenv("MT5_PATH", "")
    # Sprint 7 demo-account safety rail (run_live_demo.py): deliberately no
    # non-empty default -- an operator who has not explicitly opted in to
    # "demo" is refused, not silently allowed to trade. See
    # run_live_demo.py's _ensure_explicit_demo_configuration().
    MT5_ACCOUNT_TYPE: str = os.getenv("MT5_ACCOUNT_TYPE", "")

    # Telegram notification settings
    TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Risk management settings (Phase 6 kill-switch infrastructure)
    MAX_DAILY_LOSS_PCT: float = _parse_max_daily_loss_pct(os.getenv("MAX_DAILY_LOSS_PCT", "0.05"))

    # General configuration parameters
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    DATA_DIRECTORY: Path = Path(os.getenv("DATA_DIRECTORY", "./data"))

    # Enterprise Data Engine configuration
    TIMEZONE: str = os.getenv("TIMEZONE", "UTC")
    CSV_DELIMITER: str = os.getenv("CSV_DELIMITER", ",")
    DATETIME_FORMAT: str = os.getenv("DATETIME_FORMAT", "%Y-%m-%d %H:%M:%S")
    DUPLICATE_POLICY: str = os.getenv("DUPLICATE_POLICY", "drop")  # drop or keep
    MISSING_VALUE_POLICY: str = os.getenv("MISSING_VALUE_POLICY", "raise")  # raise, drop, or fill

    def __post_init__(self) -> None:
        """Validates policy string settings (Bug #58).

        Unlike _parse_mt5_login (which must never raise, since it runs as a
        bare default-value expression at class-definition/import time and
        would otherwise break every module that merely imports Settings),
        this runs in __post_init__, which only executes when Settings() is
        actually constructed (e.g. inside CSVDataProvider.__init__) -- so
        raising here cannot break an unrelated module's import, only the
        specific caller that's about to use a misconfigured policy. A typo'd
        DUPLICATE_POLICY/MISSING_VALUE_POLICY previously fell through every
        `if/elif` branch silently, letting duplicates/NaNs flow through
        unhandled with no warning at all.

        Raises:
            ValueError: If DUPLICATE_POLICY or MISSING_VALUE_POLICY is not
                one of its documented allowed values.
        """
        if self.DUPLICATE_POLICY not in VALID_DUPLICATE_POLICIES:
            raise ValueError(
                f"DUPLICATE_POLICY={self.DUPLICATE_POLICY!r} is invalid; "
                f"must be one of {sorted(VALID_DUPLICATE_POLICIES)}."
            )
        if self.MISSING_VALUE_POLICY not in VALID_MISSING_VALUE_POLICIES:
            raise ValueError(
                f"MISSING_VALUE_POLICY={self.MISSING_VALUE_POLICY!r} is invalid; "
                f"must be one of {sorted(VALID_MISSING_VALUE_POLICIES)}."
            )

    @classmethod
    def load(cls) -> "Settings":
        """Loads and returns an instance of settings."""
        return cls()
