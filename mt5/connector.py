"""MetaTrader 5 client terminal connector."""

import os

import MetaTrader5 as mt5  # noqa: N813
from dotenv import load_dotenv

from core.models import AccountInfo, Bar, SymbolConstraints
from mt5.rates import TIMEFRAME_MAPPING, get_symbol_point, rates_to_bars
from utils.logging import setup_logger

logger = setup_logger("mt5_connector", log_to_file=True)


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
            An AccountInfo snapshot of the connected account, including MT5's
            own trade_mode (0=demo/1=contest/2=real) -- see
            run_live_demo.py's demo-account safety rail, the only current
            reader of this field.

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
            trade_mode=info.trade_mode,
            leverage=int(getattr(info, "leverage", 0) or 0),
            login=int(info.login),
        )

    def fetch_symbol_info(self, symbol: str) -> SymbolConstraints:
        """READ-ONLY: fetches `symbol`'s contract-size/tick-value/volume constraints.

        This method NEVER places orders, modifies positions, or calls any
        MT5 trading function -- it only calls mt5.symbol_info() and the
        calculator mt5.order_calc_profit(). Safe to call against a live
        (non-demo) account. Used by execution.PositionSizer to convert a risk
        percentage into a real lot size (see execution/mt5_broker.py and
        execution/paper_broker.py's get_symbol_constraints(), which both
        delegate here).

        Args:
            symbol: Trading instrument symbol (e.g. "USTEC").

        Returns:
            A SymbolConstraints snapshot of the symbol's trading constraints.

        Raises:
            RuntimeError: If mt5.symbol_info(symbol) returns None.
        """
        info = mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"mt5.symbol_info() returned None for {symbol}. Error code: {mt5.last_error()}")

        return SymbolConstraints(
            symbol=symbol,
            contract_size=info.trade_contract_size,
            tick_size=info.trade_tick_size,
            tick_value=_tick_value(symbol, info),
            volume_min=info.volume_min,
            volume_max=info.volume_max,
            volume_step=info.volume_step,
        )


def _tick_value(symbol: str, info) -> float:
    """Account-currency value of one tick on one lot, as MT5 itself prices a trade.

    trade_tick_value cannot be trusted on its own. FundingPips reports 0.01 for
    XAUUSD (tick 0.01, 100 oz contract), i.e. $1 a point per lot, while
    order_calc_profit() and the account's real fills say $100 -- on 2026-09-08
    a 16.56-point stop on 0.06 lots lost $100.26. PositionSizer divided the risk
    budget by the understated figure, asked for ~100x the lots, and only the 20%
    margin ceiling cut that back, so gold traded at 0.8-1.9% risk against a 0.5%
    setting (4-6% at the 60m config's wider stops). NDX100, GER40 and JP225 agree
    between the two sources.

    The value is derived over a 1%-of-price move rather than a single tick,
    because order_calc_profit() rounds to account-currency cents: one JP225 tick
    is $0.000648 per lot and would round to 0.00.

    Falls back to trade_tick_value when there is no quote or the calculator
    returns nothing usable, which is the pre-existing behaviour.
    """
    reported = info.trade_tick_value
    tick_size = info.trade_tick_size
    price = getattr(info, "ask", 0.0) or getattr(info, "bid", 0.0)
    if not price or not tick_size or tick_size <= 0:
        return reported

    ticks = max(1, round(price * 0.01 / tick_size))
    try:
        profit = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, symbol, 1.0, price, price + ticks * tick_size)
    except Exception as exc:  # a metadata calc must never break sizing
        logger.warning("order_calc_profit failed for %s (%s); using reported tick_value.", symbol, type(exc).__name__)
        return reported
    if not isinstance(profit, int | float) or isinstance(profit, bool) or profit <= 0:
        return reported

    derived = profit / ticks
    if not reported or abs(derived - reported) / derived > 0.01:
        logger.warning(
            "%s: broker trade_tick_value %s disagrees with order_calc_profit (%.6g per tick); using the latter.",
            symbol, reported, derived,
        )
    return derived
