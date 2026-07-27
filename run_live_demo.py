#!/usr/bin/env python3
"""Live trading loop: Midline Sweep detection wired into TradeManager/MT5Broker.

SAFETY -- UNLIKE live_signal_check.py, THIS SCRIPT PLACES REAL (DEMO-ACCOUNT)
ORDERS. live_signal_check.py is documented read-only and safe to run against
a live (non-demo) account for signal inspection; this script is NOT -- it is
never safe to point at a real account. Two independent layers enforce this
before any order can be placed:
  1. Settings.MT5_ACCOUNT_TYPE must be explicitly set to "demo" in .env (see
     _ensure_explicit_demo_configuration()) -- checked before this script
     even opens an MT5 session.
  2. The connected account's own MT5-reported trade_mode must equal
     mt5.ACCOUNT_TRADE_MODE_DEMO (see _ensure_demo_trade_mode()) -- checked
     immediately after connecting, so a misconfigured MT5_LOGIN/MT5_SERVER
     pointing at a real account despite (1) is still caught.
Either failing raises DemoAccountRequiredError, activates the kill-switch
(risk/kill_switch.py) if a session was opened, and refuses to trade.

live_signal_check.py's own detection logic (check_signal()) is imported and
reused UNCHANGED here -- see live_signal_check.py, untouched by this sprint.

THIS IS NOT A CONTINUOUSLY-RUNNING DAEMON, same convention as
live_signal_check.py: one invocation performs exactly ONE tick (reconcile
state against the broker's actual open positions, then either manage an
already-open trade or evaluate for a new one) and exits. Task Scheduler (or
any external scheduler) re-invokes this script periodically for recurring
ticks; no loop/sleep is implemented here.

Usage:
    python run_live_demo.py --symbol USTEC --timeframe M5 --volume 0.1
"""

import argparse
import sys
from pathlib import Path

# Add project root to python path so the script also works when invoked
# directly (python run_live_demo.py) rather than as a module.
sys.path.append(str(Path(__file__).parent.resolve()))

import MetaTrader5 as mt5  # noqa: N813

from config.settings import Settings
from core.models import AccountInfo, Bar, OrderType, SignalDirection, Timeframe
from execution.interfaces import IBroker
from execution.models import Position, TradeManagerAction
from execution.mt5_broker import MT5Broker
from execution.order import OrderStatus
from execution.trade_manager import TradeManager
from live_signal_check import (
    check_data_quality_and_alert,
    check_signal,
    format_setup,
    send_telegram_alert,
)
from mt5.connector import MT5Connector
from risk.daily_risk_tracker import DailyRiskTracker
from risk.kill_switch import activate_kill_switch, is_trading_halted
from strategy.diagnostics import top_rejection_reasons
from strategy.nasdaq_midline_sweep import NasdaqMidlineSweepStrategy
from utils.logging import setup_logger, setup_structured_logger

logger = setup_logger("run_live_demo", log_to_file=True)

# Structured JSON event stream for every loop decision point (Sprint 6c's T2
# convention, reused unchanged -- see execution/event_log.py,
# core/data_quality.py's data_quality_events for precedent). Aggregable
# later the same way execution_events.log supports per-fill slippage
# aggregation.
trade_events_logger = setup_structured_logger("trade_events")

DEFAULT_LOOKBACK_BARS = 1000
DEFAULT_BODY_MULTIPLIER = 1.5
DEFAULT_VOLUME = 0.1


class DemoAccountRequiredError(RuntimeError):
    """Raised when this script is not explicitly and verifiably pointed at a demo account."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parses command-line arguments for the live demo trading loop."""
    parser = argparse.ArgumentParser(
        description="Live Midline Sweep trading loop against a DEMO MT5 account "
        "(places real demo orders -- see module docstring)."
    )
    parser.add_argument("--symbol", default="USTEC", help="Trading instrument symbol (default: USTEC).")
    parser.add_argument("--timeframe", default="M5", help="Bar timeframe (default: M5).")
    parser.add_argument(
        "--lookback-bars",
        type=int,
        default=DEFAULT_LOOKBACK_BARS,
        help=f"Bars to fetch/replay before evaluating the final bar (default: {DEFAULT_LOOKBACK_BARS}).",
    )
    parser.add_argument(
        "--body-multiplier",
        type=float,
        default=DEFAULT_BODY_MULTIPLIER,
        help=f"NasdaqMidlineSweepStrategy body_multiplier (default: {DEFAULT_BODY_MULTIPLIER}, "
        "the validated USTEC OOS setting).",
    )
    parser.add_argument(
        "--volume",
        type=float,
        default=DEFAULT_VOLUME,
        help=f"TradeManager position size (lots) for every new trade (default: {DEFAULT_VOLUME}).",
    )
    return parser.parse_args(argv)


def _ensure_explicit_demo_configuration() -> None:
    """Requires Settings.MT5_ACCOUNT_TYPE to be explicitly set to "demo".

    The first, cheapest layer of the two-layer demo-account safety rail --
    checked before this script does anything else, including checking the
    kill-switch or opening an MT5 session. Fails loudly rather than
    defaulting to a permissive value: MT5_ACCOUNT_TYPE has no non-empty
    default (see config/settings.py), so an operator who has not
    deliberately configured it is refused, not silently allowed through.
    See _ensure_demo_trade_mode() for the second, MT5-verified layer.

    Raises:
        DemoAccountRequiredError: If Settings.MT5_ACCOUNT_TYPE
            (case-insensitive, whitespace-trimmed) is not exactly "demo".
    """
    account_type = Settings.load().MT5_ACCOUNT_TYPE.strip().lower()
    if account_type != "demo":
        raise DemoAccountRequiredError(
            f"MT5_ACCOUNT_TYPE must be explicitly set to 'demo' in .env to run "
            f"run_live_demo.py (got {account_type!r}). Refusing to start."
        )


def _ensure_demo_trade_mode(account_info: AccountInfo) -> None:
    """Cross-checks the connected MT5 account's own reported trade_mode.

    The second layer of the demo-account safety rail, layered on top of
    _ensure_explicit_demo_configuration()'s .env-level gate: that check only
    confirms the OPERATOR intended a demo account -- it cannot detect a
    misconfigured MT5_LOGIN/MT5_SERVER pair that actually connects to a real
    account despite MT5_ACCOUNT_TYPE=demo being set. MT5 itself exposes the
    true account type via account_info().trade_mode (0=demo/1=contest/
    2=real) -- see mt5.connector.MT5Connector.fetch_account_info(),
    core.models.AccountInfo.trade_mode. A real MT5 account always populates
    this field, so None here (e.g. from a source that never sets it) is
    also treated as unsafe -- "don't default silently" applies just as much
    to an unknown trade_mode as to a wrong one.

    Args:
        account_info: The connected account's info, e.g. from
            MT5Broker.get_account_info().

    Raises:
        DemoAccountRequiredError: If trade_mode is not exactly
            mt5.ACCOUNT_TRADE_MODE_DEMO.
    """
    if account_info.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        raise DemoAccountRequiredError(
            f"Connected MT5 account does not report a DEMO trade_mode "
            f"(got {account_info.trade_mode!r}, expected "
            f"{mt5.ACCOUNT_TRADE_MODE_DEMO} = ACCOUNT_TRADE_MODE_DEMO). "
            "Refusing to trade -- this account may be LIVE."
        )


def _direction_from_order_type(order_type: OrderType) -> SignalDirection:
    """Maps a Position's OrderType back to the SignalDirection TradeManager tracks."""
    return SignalDirection.BUY if order_type == OrderType.BUY_MARKET else SignalDirection.SELL


def _attach_to_open_position(trade_manager: TradeManager, broker: IBroker, position: Position) -> None:
    """Rehydrates trade_manager's tracked-trade state from a broker-reported Position.

    TradeManager is an in-process, single-run lifecycle owner (Sprint 3-4)
    with no persistence of its own -- see execution/trade_manager.py. Since
    this script is re-invoked fresh by Task Scheduler on every tick
    (matching live_signal_check.py's one-shot-per-invocation pattern, see
    module docstring), a brand-new TradeManager() never remembers a trade
    opened by a PREVIOUS invocation's process.

    The broker itself is the actual source of truth for what's open -- MT5's
    own server for MT5Broker, or PaperBroker's persisted state file -- so
    querying broker.get_open_positions() every tick (see run_once()) is used
    instead of maintaining a second, potentially-drifting local state file
    just for this script. This function resyncs TradeManager's tracked
    fields from that ground truth immediately before delegating to its own
    on_new_bar()/_check_levels() SL/TP logic, so that logic is reused
    unchanged rather than duplicated here.

    Args:
        trade_manager: A freshly constructed TradeManager (has_open_trade is
            False) to attach to `position`.
        broker: The IBroker `position` was read from; stored so
            trade_manager can later call close_position() on it.
        position: The open position to attach to, as returned by
            broker.get_open_positions().
    """
    trade_manager._broker = broker
    trade_manager._position_id = position.id
    trade_manager._direction = _direction_from_order_type(position.order_type)
    trade_manager._stop_loss = position.stop_loss
    trade_manager._take_profit = position.take_profit


def _log_trade_event(event: str, **fields: object) -> None:
    """Emits one structured JSON line to logs/trade_events.log for a loop decision point."""
    trade_events_logger.info({"event_type": event, **fields})


def _manage_open_trade(
    trade_manager: TradeManager, broker: IBroker, position: Position, final_bar: Bar
) -> None:
    """Checks an already-open position's SL/TP against the latest bar and closes if hit.

    A hit level does not always mean the position ends this tick: TradeManager
    reports TradeManagerAction.CLOSE_FAILED (not CLOSED_SL/CLOSED_TP) when the
    broker declines the close, in which case the position is still open and
    is picked up again -- and another close retried -- on the next tick's
    broker.get_open_positions() reconciliation.

    Args:
        trade_manager: A freshly constructed TradeManager to attach to
            `position` (see _attach_to_open_position()).
        broker: The IBroker `position` was read from.
        position: The open position to manage.
        final_bar: The most recently closed Bar to check levels against.
    """
    symbol = position.symbol
    if position.stop_loss is None or position.take_profit is None:
        logger.error(
            "Open position %s for %s has no stop_loss/take_profit set; cannot manage it this tick.",
            position.id,
            symbol,
        )
        _log_trade_event("unmanageable_position", symbol=symbol, position_id=position.id)
        return

    _attach_to_open_position(trade_manager, broker, position)
    action = trade_manager.on_new_bar(final_bar)

    if action is TradeManagerAction.HELD:
        logger.info("Trade %s for %s held (price within SL/TP).", position.id, symbol)
        _log_trade_event("held", symbol=symbol, position_id=position.id)
    elif action is TradeManagerAction.CLOSE_FAILED:
        close_result = trade_manager.last_close_result
        reason = close_result.comment if close_result is not None else "unknown"
        retcode = close_result.retcode if close_result is not None else None
        logger.error(
            "Trade %s for %s FAILED TO CLOSE: %s (retcode=%s) -- position remains open, "
            "will retry next tick.",
            position.id,
            symbol,
            reason,
            retcode,
        )
        _log_trade_event(
            "close_failed", symbol=symbol, position_id=position.id, reason=reason, retcode=retcode
        )
    else:
        logger.info("Trade %s for %s closed: %s", position.id, symbol, action.value)
        _log_trade_event("closed", symbol=symbol, position_id=position.id, outcome=action.value)


def _evaluate_for_new_trade(
    trade_manager: TradeManager,
    broker: IBroker,
    strategy: NasdaqMidlineSweepStrategy,
    bars: list[Bar],
    symbol: str,
    timeframe: Timeframe,
) -> None:
    """Evaluates the latest bar for a new signal and opens a trade if one fires.

    A true no-op if no signal fires. If a signal fires but the kill-switch
    is active, the signal is still reported (logged, printed, alerted) but
    no order is placed -- this is where the kill-switch/daily-risk checks
    actually gate order placement, not just log it (Sprint 7 requirement).

    Args:
        trade_manager: The TradeManager to open a trade with, if a signal fires.
        broker: The IBroker to open the trade through.
        strategy: The configured NasdaqMidlineSweepStrategy instance.
        bars: Chronologically ordered bars, ending with the most recently closed bar.
        symbol: Trading instrument symbol.
        timeframe: Bar timeframe.
    """
    setup, diagnostics, _final_bar = check_signal(bars, symbol, timeframe, strategy)

    if setup is None:
        reasons = top_rejection_reasons(diagnostics)
        reasons_str = ", ".join(f"{r} ({c})" for r, c in reasons) if reasons else "no diagnostics recorded"
        logger.info("RESULT: NO SIGNAL (top reason: %s)", reasons_str)
        _log_trade_event("no_signal", symbol=symbol)
        return

    print(format_setup(setup))
    logger.info(
        "RESULT: SIGNAL %s %s @ %s", setup.symbol, setup.direction.name, setup.timestamp
    )
    _log_trade_event(
        "signal_found",
        symbol=symbol,
        direction=setup.direction.name,
        setup_id=setup.setup_id,
    )
    send_telegram_alert(setup)

    if is_trading_halted():
        logger.warning(
            "Signal found for %s but the kill-switch is active; refusing to open a new trade.", symbol
        )
        _log_trade_event("signal_blocked_kill_switch", symbol=symbol, setup_id=setup.setup_id)
        return

    order = trade_manager.open_trade(setup, broker)
    if order.status is OrderStatus.FILLED:
        assert order.fill_price is not None  # fill() always sets it before FILLED is reachable
        # A real order genuinely succeeded -- explicitly confirm the day's
        # one-trade guard as consumed (check_signal()'s replay no longer does
        # this implicitly on a merely-discarded setup; see
        # NasdaqMidlineSweepStrategy.evaluate()'s record_trade_taken docstring).
        strategy.mark_trade_taken()
        logger.info(
            "Trade opened for %s: order_id=%s fill_price=%.5f", symbol, order.order_id, order.fill_price
        )
        _log_trade_event(
            "trade_opened",
            symbol=symbol,
            setup_id=setup.setup_id,
            order_id=order.order_id,
            fill_price=order.fill_price,
        )
    else:
        open_result = trade_manager.last_open_result
        reason = open_result.comment if open_result is not None else "unknown"
        retcode = open_result.retcode if open_result is not None else None
        logger.error(
            "Trade open REJECTED for %s: order_id=%s reason=%s (retcode=%s)",
            symbol,
            order.order_id,
            reason,
            retcode,
        )
        _log_trade_event(
            "trade_open_rejected",
            symbol=symbol,
            setup_id=setup.setup_id,
            order_id=order.order_id,
            reason=reason,
            retcode=retcode,
        )


def run_once(
    connector: MT5Connector,
    broker: IBroker,
    trade_manager: TradeManager,
    strategy: NasdaqMidlineSweepStrategy,
    symbol: str,
    timeframe: Timeframe,
    timeframe_str: str,
    lookback_bars: int,
) -> None:
    """Executes exactly one tick of the live trading loop.

    Fetches the latest bars, reconciles state against the broker's own
    ground truth (broker.get_open_positions()), then either manages an
    already-open trade's SL/TP or evaluates for a new signal. Broker-
    agnostic (IBroker) -- callers pass MT5Broker for real demo trading (see
    main()) or PaperBroker for tests/paper trading, with no code-path
    differences.

    Does NOT perform the MT5_ACCOUNT_TYPE / trade_mode demo-account safety
    checks -- those are main()'s responsibility, run once before the broker
    is ever touched (see _ensure_explicit_demo_configuration(),
    _ensure_demo_trade_mode()); this function assumes that gate has already
    passed.

    Args:
        connector: Used for the read-only bar fetch (IBroker has no
            bar-fetching method of its own -- see execution/interfaces.py).
        broker: The IBroker to manage/open trades through.
        trade_manager: A freshly constructed TradeManager for this tick (see
            _attach_to_open_position()'s docstring for why "freshly
            constructed" matters).
        strategy: The configured NasdaqMidlineSweepStrategy instance.
        symbol: Trading instrument symbol.
        timeframe: Bar Timeframe enum value.
        timeframe_str: The raw timeframe string (e.g. "M5"), for
            fetch_recent_bars()/data-quality alert formatting.
        lookback_bars: Bars to fetch before evaluating the final bar.
    """
    bars = connector.fetch_recent_bars(symbol, timeframe_str, lookback_bars)
    logger.info(
        "Fetched %d bar(s) for %s [%s]: %s -> %s",
        len(bars),
        symbol,
        timeframe_str,
        bars[0].timestamp,
        bars[-1].timestamp,
    )
    check_data_quality_and_alert(bars, symbol, timeframe, timeframe_str)

    open_positions = [p for p in broker.get_open_positions() if p.symbol == symbol]

    if len(open_positions) > 1:
        logger.error(
            "Ambiguous open positions for %s (%d found); skipping this tick.", symbol, len(open_positions)
        )
        _log_trade_event("ambiguous_positions", symbol=symbol, count=len(open_positions))
        return

    if len(open_positions) == 1:
        _manage_open_trade(trade_manager, broker, open_positions[0], bars[-1])
        return

    _evaluate_for_new_trade(trade_manager, broker, strategy, bars, symbol, timeframe)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: one tick of the live demo trading loop, then exits."""
    args = parse_args(argv)
    timeframe = Timeframe[args.timeframe]

    try:
        _ensure_explicit_demo_configuration()
    except DemoAccountRequiredError as exc:
        logger.critical("DEMO-ACCOUNT SAFETY RAIL TRIPPED (config): %s", exc)
        print(f"REFUSING TO START: {exc}")
        sys.exit(1)

    if is_trading_halted():
        logger.info("RESULT: TRADING HALTED (kill-switch active)")
        print("TRADING HALTED (kill-switch active)")
        return

    connector = MT5Connector()
    broker = MT5Broker(connector=connector)

    if not broker.connect():
        logger.error("Could not connect to MT5.")
        sys.exit(1)

    try:
        account_info = broker.get_account_info()
        try:
            _ensure_demo_trade_mode(account_info)
        except DemoAccountRequiredError as exc:
            logger.critical("DEMO-ACCOUNT SAFETY RAIL TRIPPED (MT5 account): %s", exc)
            activate_kill_switch(f"run_live_demo.py: {exc}")
            print(f"REFUSING TO TRADE: {exc}")
            sys.exit(1)
        # Diagnostic evidence (2026-07-22 incident): the safety rail's PASS
        # path previously left no trace in logs -- there was no way to tell,
        # after the fact, whether this check had genuinely evaluated and
        # matched vs. been silently skipped. Purely additive logging; the
        # gating logic above (_ensure_demo_trade_mode) is unchanged.
        logger.info(
            "Demo-account safety rail passed: trade_mode=%s (expected "
            "ACCOUNT_TRADE_MODE_DEMO=%s), currency=%s, equity=%.2f.",
            account_info.trade_mode,
            mt5.ACCOUNT_TRADE_MODE_DEMO,
            account_info.currency,
            account_info.equity,
        )

        DailyRiskTracker().check_and_update(account_info.equity)

        strategy = NasdaqMidlineSweepStrategy(body_multiplier=args.body_multiplier)
        trade_manager = TradeManager(volume=args.volume)
        run_once(
            connector=connector,
            broker=broker,
            trade_manager=trade_manager,
            strategy=strategy,
            symbol=args.symbol,
            timeframe=timeframe,
            timeframe_str=args.timeframe,
            lookback_bars=args.lookback_bars,
        )
    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()
