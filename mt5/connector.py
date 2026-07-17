"""MetaTrader 5 client terminal connector."""

import os

import MetaTrader5 as mt5  # noqa: N813
from dotenv import load_dotenv

from core.models import AccountInfo, Bar
from mt5.rates import TIMEFRAME_MAPPING, get_symbol_point, rates_to_bars
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

    def fetch_recent_bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
        """READ-ONLY: fetches the last `count` fully CLOSED bars for `symbol`, up to now.

        This method NEVER places orders, modifies positions, or calls any
        MT5 trading function (order_send, order_check, etc.) -- it only calls
        mt5.symbol_select(), mt5.symbol_info(), and mt5.copy_rates_from_pos().
        Safe to call against a live (non-demo) account for signal inspection.

        Uses start_pos=1 (not 0): MT5's position 0 is the currently-forming,
        not-yet-closed bar, whose OHLC (especially close) is still changing.
        Evaluating a strategy against an incomplete bar risks a premature or
        false signal, so this always skips it and returns only bars that have
        fully closed.

        Args:
            symbol: Trading instrument symbol (e.g. "USTEC").
            timeframe: One of the supported timeframe keys (e.g. "M5").
            count: Number of most-recent closed bars to fetch.

        Returns:
            Chronologically ordered (oldest first) list of Bar objects.

        Raises:
            RuntimeError: If timeframe is unsupported, the symbol cannot be
                selected, or no rates are returned.
        """
        if timeframe not in TIMEFRAME_MAPPING:
            raise RuntimeError(f"Unsupported timeframe: {timeframe}")
        mt5_tf = TIMEFRAME_MAPPING[timeframe]

        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Symbol {symbol} is not available in the MT5 terminal.")

        point = get_symbol_point(symbol)

        rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 1, count)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"No recent rates returned from MT5 for {symbol} {timeframe}.")

        return rates_to_bars(rates, point)

    def fetch_account_info(self) -> AccountInfo:
        """READ-ONLY: fetches the connected account's current balance/equity/margin.

        This method NEVER places orders, modifies positions, or calls any
        MT5 trading function -- it only calls mt5.account_info(). Safe to
        call against a live (non-demo) account for risk monitoring.

        Deliberately raises rather than returning a zero-valued AccountInfo
        on failure: a silent zero balance/equity could otherwise be
        misread by a caller (e.g. DailyRiskTracker) as a 100% loss and
        wrongly trigger the kill-switch.

        Returns:
            An AccountInfo snapshot of the connected account.

        Raises:
            RuntimeError: If mt5.account_info() returns None (not connected,
                or the terminal rejected the request).
        """
        info = mt5.account_info()
        if info is None:
            raise RuntimeError(f"mt5.account_info() returned None. Error code: {mt5.last_error()}")

        return AccountInfo(
            balance=info.balance,
            equity=info.equity,
            margin=info.margin,
            free_margin=info.margin_free,
            margin_level=info.margin_level,
            currency=info.currency,
        )
