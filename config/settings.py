"""Framework settings loader.

Parses configuration values from environment variables and environment files (.env).
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Read-only system configurations loaded from environment settings."""

    # MetaTrader 5 execution settings
    MT5_LOGIN: int = int(os.getenv("MT5_LOGIN", "0"))
    MT5_PASSWORD: str = os.getenv("MT5_PASSWORD", "")
    MT5_SERVER: str = os.getenv("MT5_SERVER", "MetaQuotes-Demo")
    MT5_PATH: str = os.getenv("MT5_PATH", "")

    # Telegram notification settings
    TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

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

    @classmethod
    def load(cls) -> "Settings":
        """Loads and returns an instance of settings."""
        return cls()
