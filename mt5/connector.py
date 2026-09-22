"""MetaTrader 5 client terminal connector."""

import os

import MetaTrader5 as mt5  # noqa: N813
from dotenv import load_dotenv

import config.brokers as machine
from config.brokers import BrokerProfile
from core.models import AccountInfo, Bar, SymbolConstraints
from mt5.rates import TIMEFRAME_MAPPING, get_symbol_point, rates_to_bars
from utils.logging import setup_logger

logger = setup_logger("mt5_connector", log_to_file=True)


class WrongBrokerError(RuntimeError):
    """The terminal is logged into an account other than the one .env names."""


class WrongTerminalError(RuntimeError):
    """MT5 attached to a terminal installation other than the one .env's MT5_PATH names."""


def terminal_folder(path: str) -> str:
    """The installation folder MT5_PATH names; it may point at terminal64.exe or at its folder."""
    folder = os.path.dirname(path) if path.lower().endswith(".exe") else path
    return os.path.normcase(os.path.normpath(folder))


def initialize_terminal() -> bool:
    """mt5.initialize() on THIS checkout's terminal, and only while it is on this checkout's broker.

    Since 2026-09-22 one VPS runs two brokers: C:\\tradebot on CFI's terminal and C:\\tradebot_fp
    on FundingPips', each with its own .env. A bare mt5.initialize() lets the MetaTrader5 package
    pick a terminal by itself, which with two installed is whichever it finds. connect() then
    calls mt5.login() with ITS .env's account, and that would switch the other broker's terminal
    onto this one -- under a bot that already checked which account it was on. So:

      - with MT5_PATH set, the terminal that answered must be the one installed there;
      - a terminal already logged into a server other than .env's MT5_SERVER is refused, never
        switched. No bot here needs to move a terminal between brokers: a person does that by
        hand when the broker changes (deploy/README.md).

    Every script that reads an account goes through this, not only the bots: the weekly report
    and the kill rule read "the connected account", and a report on the wrong one is silent.

    Returns:
        True when attached, False when mt5.initialize() itself failed (mt5.last_error() says why).

    Raises:
        WrongTerminalError: The terminal that answered is not installed at MT5_PATH.
        WrongBrokerError: The terminal is logged into a server other than MT5_SERVER.
    """
    load_dotenv()
    path = os.getenv("MT5_PATH", "")
    server = os.getenv("MT5_SERVER", "")

    if not (mt5.initialize(path=path) if path else mt5.initialize()):
        return False

    if path:
        info = mt5.terminal_info()
        actual = getattr(info, "path", None)
        if not actual or os.path.normcase(os.path.normpath(actual)) != terminal_folder(path):
            mt5.shutdown()
            raise WrongTerminalError(
                f"MT5_PATH names the terminal in {os.path.dirname(path) or path}, but the one "
                f"that answered is installed in {actual!r}. Refusing to use another broker's terminal."
            )

    account = mt5.account_info()
    if server and account is not None and account.server != server:
        mt5.shutdown()
        raise WrongBrokerError(
            f"the terminal is logged into {account.server!r}, but .env names {server!r}. Refusing "
            f"to log it into another broker -- check MT5_PATH, or log in by hand if the broker changed."
        )
    return True


def resolve_ticker(symbol: str) -> tuple[BrokerProfile, str]:
    """This machine's broker, and the ticker IT uses for the name a launcher passed.

    Called before connecting, because a bot's state files are named from the ticker: the VPS's
    CFI gold keeps `XAUUSD_` files and this workstation's FundingPips gold keeps `XAUUSD` ones,
    so two brokers can never read each other's open paper position or daily risk baseline.
    """
    profile = machine.local()
    return profile, profile.ticker(symbol)


def ensure_logged_into(profile: BrokerProfile) -> None:
    """Refuses to go on unless the terminal really is on the account .env names.

    connect() logs in with MT5_SERVER, so this should never fire -- but everything downstream
    (which ticker to poll, which state files are this bot's, which broker's specs the reports
    read) is derived from that one string, and being wrong about it means trading the wrong
    account. Cheap assertion, expensive failure.

    Raises:
        WrongBrokerError: If the terminal reports a different server.
    """
    info = mt5.account_info()
    if info is None:
        raise WrongBrokerError("MT5 returned no account_info() -- cannot confirm which broker this is.")
    if info.server != profile.server:
        raise WrongBrokerError(
            f".env names {profile.server} ({profile.name}), but the terminal is logged into "
            f"{info.server!r}. Refusing to trade the wrong account."
        )


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

        try:
            login = int(login_str)
        except ValueError:
            logger.error("MT5_LOGIN must be a valid integer, got %s", login_str)
            return False

        logger.info("Initializing MT5 terminal...")

        try:
            init_success = initialize_terminal()
        except (WrongTerminalError, WrongBrokerError) as exc:
            logger.error("%s", exc)
            return False

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
