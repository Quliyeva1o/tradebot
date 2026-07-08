"""MetaTrader 5 client terminal connector."""

import os

import MetaTrader5 as mt5  # noqa: N813
from dotenv import load_dotenv

from utils.logging import setup_logger

logger = setup_logger("mt5_connector")


class MT5Connector:
    """Manages active socket and session linkages to a running MT5 platform client."""

    def __init__(self) -> None:
        """Initializes the MT5Connector."""
        self._connected = False

    def connect(self) -> bool:
        """Connects to the MT5 terminal using credentials loaded exclusively from .env.

        Returns:
            True if connection initialized successfully, False otherwise.
        """
        load_dotenv()

        login_str = os.getenv("MT5_LOGIN", "0")
        password = os.getenv("MT5_PASSWORD", "")
        server = os.getenv("MT5_SERVER", "MetaQuotes-Demo")
        path = os.getenv("MT5_PATH", "")

        try:
            login = int(login_str)
        except ValueError:
            logger.error("MT5_LOGIN must be a valid integer, got %s", login_str)
            return False

        logger.info("Initializing MT5 terminal...")

        # Initialize terminal
        if path:
            init_success = mt5.initialize(path=path)
        else:
            init_success = mt5.initialize()

        if not init_success:
            logger.error("Failed to initialize MT5 terminal. Error code: %s", mt5.last_error())
            return False

        # Attempt to login
        login_success = mt5.login(login=login, password=password, server=server)
        if not login_success:
            logger.error(
                "MT5 login failed for account %d on server %s. Error code: %s",
                login,
                server,
                mt5.last_error(),
            )
            mt5.shutdown()
            return False

        logger.info("Successfully connected and logged into MT5 account %d", login)
        self._connected = True
        return True

    def disconnect(self) -> None:
        """Gracefully disconnects from MT5 terminal."""
        if self._connected:
            mt5.shutdown()
            self._connected = False
            logger.info("Disconnected from MT5 terminal.")
