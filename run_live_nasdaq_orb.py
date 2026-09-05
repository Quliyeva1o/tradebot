#!/usr/bin/env python3
"""Live trading loop: NASDAQ ORB M1 Breakout strategy (see
strategy/nasdaq_orb_m1_breakout.py's module docstring for the full
validation) wired into TradeManager/MT5Broker -- structural clone of
run_live_xauusd_orb.py's pattern, with NasdaqOrbM1BreakoutStrategy swapped
in and an M1 (not M15) fetch.

SAFETY -- THIS SCRIPT PLACES REAL (DEMO-ACCOUNT) ORDERS UNLESS --paper IS
PASSED. See run_live_demo.py's module docstring for the full two-layer
demo-account enforcement description; reused unchanged here.

NOT YET forward/paper-validated -- fresh backtest-to-live port. Run with
--paper for an extended trial BEFORE ever running without that flag.

--tp-r has no single default: the 2026-09-04 108-combo sweep found the best
R varies by symbol (XAUUSD ~4.0, GER40 ~3.0, see strategy module docstring)
-- always pass it explicitly per symbol rather than relying on the fallback.

Usage:
    python run_live_nasdaq_orb.py --symbol XAUUSD --tp-r 4.0 --risk-per-trade-pct 0.005 --paper
    python run_live_nasdaq_orb.py --symbol GER40 --tp-r 3.0 --risk-per-trade-pct 0.01 --paper
"""

import argparse
import sys
from pathlib import Path

import MetaTrader5 as mt5  # noqa: N813

from config.settings import Settings
from core.models import AccountInfo, Bar, OrderType, SignalDirection, Timeframe
from execution.interfaces import IBroker
from execution.models import Position, TradeManagerAction
from execution.mt5_broker import MT5Broker
from execution.order import OrderStatus
from execution.paper_broker import PaperBroker
from execution.position_sizer import PositionSizer
from execution.trade_manager import TradeManager
from mt5.connector import MT5Connector
from risk.daily_risk_tracker import DailyRiskTracker
from risk.kill_switch import activate_kill_switch, is_trading_halted
from strategy.diagnostics import top_rejection_reasons
from strategy.nasdaq_orb_m1_breakout import NasdaqOrbM1BreakoutConfig, NasdaqOrbM1BreakoutStrategy
from utils.logging import setup_logger, setup_structured_logger
from market_structure.structure_models import MarketState

logger = setup_logger("run_live_nasdaq_orb", log_to_file=True)
trade_events_logger = setup_structured_logger("trade_events")

# M1 lookback: 2 days comfortably covers today's 09:30 NY Opening Range from
# any poll time (NY 09:30 is at most ~24h before "now" for a same-day poll,
# plus DST/weekend margin), at a trivial per-invocation replay size
# (2 * 1440 M1 bars/day = 2880).
DEFAULT_LOOKBACK_DAYS = 2
DEFAULT_VOLUME = 0.1
DEFAULT_RISK_PER_TRADE_PCT = 0.005

_CURRENT_MODE = "live"


class DemoAccountRequiredError(RuntimeError):
    """Raised when this script is not explicitly and verifiably pointed at a demo account."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Live NASDAQ ORB M1 Breakout loop against a DEMO MT5 account "
        "(places real demo orders -- see module docstring)."
    )
    parser.add_argument("--symbol", required=True, help="MT5 symbol name (this account's ticker)")
    parser.add_argument("--tp-r", type=float, required=True, help="Take-profit R multiple (symbol-specific, see module docstring)")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--volume", type=float, default=DEFAULT_VOLUME)
    parser.add_argument("--risk-per-trade-pct", type=float, default=DEFAULT_RISK_PER_TRADE_PCT)
    parser.add_argument("--direction", default="long", choices=["long", "short", "both"])
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Use PaperBroker (virtual fills against real MT5 prices, no real orders) instead of "
        "MT5Broker. REQUIRED for now -- this strategy has NOT yet run in any live/paper capacity.",
    )
    return parser.parse_args(argv)


def _ensure_explicit_demo_configuration() -> None:
    """Identical gate to run_live_demo.py -- see that module for the full rationale."""
    account_type = Settings.load().MT5_ACCOUNT_TYPE.strip().lower()
    if account_type != "demo":
        raise DemoAccountRequiredError(
            f"MT5_ACCOUNT_TYPE must be explicitly set to 'demo' in .env to run "
            f"run_live_nasdaq_orb.py (got {account_type!r}). Refusing to start."
        )


def _ensure_demo_trade_mode(account_info: AccountInfo) -> None:
    """Identical gate to run_live_demo.py -- see that module for the full rationale."""
    if account_info.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        raise DemoAccountRequiredError(
            f"Connected MT5 account does not report a DEMO trade_mode "
            f"(got {account_info.trade_mode!r}, expected "
            f"{mt5.ACCOUNT_TRADE_MODE_DEMO} = ACCOUNT_TRADE_MODE_DEMO). "
            "Refusing to trade -- this account may be LIVE."
        )


def _direction_from_order_type(order_type: OrderType) -> SignalDirection:
    return SignalDirection.BUY if order_type == OrderType.BUY_MARKET else SignalDirection.SELL


def _attach_to_open_position(trade_manager: TradeManager, broker: IBroker, position: Position) -> None:
    trade_manager._broker = broker
    trade_manager._position_id = position.id
    trade_manager._direction = _direction_from_order_type(position.order_type)
    trade_manager._stop_loss = position.stop_loss
    trade_manager._take_profit = position.take_profit


def _log_trade_event(event: str, **fields: object) -> None:
    trade_events_logger.info({"event_type": event, "mode": _CURRENT_MODE, **fields})


# Every setup_id NasdaqOrbM1BreakoutStrategy emits starts with this (see
# _build_setup in strategy/nasdaq_orb_m1_breakout.py), and
# TradeManager.open_trade sends the setup_id as the order comment, so it
# travels back on the open Position.
#
# MUST be <=20 chars: MT5Broker._mt5_comment() truncates any comment over
# _MT5_COMMENT_MAX_LENGTH=29 to its first 20 chars + "_" + an 8-hex-char
# hash -- see run_live_xauusd_orb.py's STRATEGY_TAG docstring for the exact
# bug this budget guards against. "setup_nasdaq_orb_m1" is 19 chars.
STRATEGY_TAG = "setup_nasdaq_orb_m1"


def _partition_positions(
    positions: list[Position], symbol: str, tag: str = STRATEGY_TAG
) -> tuple[list[Position], list[Position]]:
    """Splits this symbol's open positions into (ours, someone-else's) --
    see run_live_sr_bias.py's identical function for the full multi-bot-on-
    one-account rationale.
    """
    same_symbol = [p for p in positions if p.symbol == symbol]
    mine = [p for p in same_symbol if p.comment.startswith(tag)]
    foreign = [p for p in same_symbol if not p.comment.startswith(tag)]
    return mine, foreign


def _manage_open_trade(trade_manager: TradeManager, broker: IBroker, position: Position, bars: list[Bar]) -> None:
    """Checks the open position's SL/TP against every bar closed SINCE it
    opened, not just the newest one -- see run_live_sr_bias.py's identical
    function docstring for the full rationale.
    """
    symbol = position.symbol
    if position.stop_loss is None or position.take_profit is None:
        logger.error("Open position %s for %s has no stop_loss/take_profit; cannot manage.", position.id, symbol)
        _log_trade_event("unmanageable_position", symbol=symbol, position_id=position.id)
        return

    _attach_to_open_position(trade_manager, broker, position)

    relevant_bars = [b for b in bars if b.timestamp > position.timestamp] or bars[-1:]
    action = TradeManagerAction.HELD
    for b in relevant_bars:
        action = trade_manager.on_new_bar(b)
        if action is not TradeManagerAction.HELD:
            break

    if action is TradeManagerAction.HELD:
        logger.info("Trade %s for %s held.", position.id, symbol)
        _log_trade_event("held", symbol=symbol, position_id=position.id)
    elif action is TradeManagerAction.CLOSE_FAILED:
        close_result = trade_manager.last_close_result
        reason = close_result.comment if close_result is not None else "unknown"
        retcode = close_result.retcode if close_result is not None else None
        logger.error("Trade %s for %s FAILED TO CLOSE: %s (retcode=%s).", position.id, symbol, reason, retcode)
        _log_trade_event("close_failed", symbol=symbol, position_id=position.id, reason=reason, retcode=retcode)
    else:
        logger.info("Trade %s for %s closed: %s", position.id, symbol, action.value)
        _log_trade_event("closed", symbol=symbol, position_id=position.id, outcome=action.value)


def _evaluate_for_new_trade(
    trade_manager: TradeManager,
    broker: IBroker,
    strategy: NasdaqOrbM1BreakoutStrategy,
    bars: list[Bar],
    symbol: str,
    timeframe: Timeframe,
    kill_switch_flag_path: Path | None = None,
) -> None:
    """Replays every fetched bar through the strategy in chronological order
    (a fresh strategy instance only ever sees ONE bar per evaluate() call
    otherwise, so its day-scoped state would never accumulate), and acts
    only on the setup -- if any -- returned for the FINAL (newest) bar.
    """
    market_state = MarketState(symbol=symbol, timeframe=timeframe)
    setup = None
    for b in bars:
        market_state.append_bar(b)
        setup = strategy.evaluate(market_state)

    if setup is None:
        reasons = top_rejection_reasons({"strategy": strategy.diagnostics.summary()})
        reasons_str = ", ".join(f"{r} ({c})" for r, c in reasons) if reasons else "no diagnostics recorded"
        logger.info("RESULT: NO SIGNAL (top reason: %s)", reasons_str)
        _log_trade_event("no_signal", symbol=symbol)
        return

    logger.info("RESULT: SIGNAL %s %s @ %s", setup.symbol, setup.direction.name, setup.timestamp)
    _log_trade_event("signal_found", symbol=symbol, direction=setup.direction.name, setup_id=setup.setup_id)

    if is_trading_halted(kill_switch_flag_path):
        logger.warning("Signal found for %s but kill-switch is active; refusing to open.", symbol)
        _log_trade_event("signal_blocked_kill_switch", symbol=symbol, setup_id=setup.setup_id)
        return

    order = trade_manager.open_trade(setup, broker)
    if order.status is OrderStatus.FILLED:
        assert order.fill_price is not None
        logger.info("Trade opened for %s: order_id=%s fill_price=%.5f", symbol, order.order_id, order.fill_price)
        _log_trade_event("trade_opened", symbol=symbol, setup_id=setup.setup_id, order_id=order.order_id, fill_price=order.fill_price)
    else:
        open_result = trade_manager.last_open_result
        reason = open_result.comment if open_result is not None else "unknown"
        retcode = open_result.retcode if open_result is not None else None
        logger.error("Trade open REJECTED for %s: reason=%s (retcode=%s)", symbol, reason, retcode)
        _log_trade_event("trade_open_rejected", symbol=symbol, setup_id=setup.setup_id, order_id=order.order_id, reason=reason, retcode=retcode)


def run_once(
    connector: MT5Connector,
    broker: IBroker,
    trade_manager: TradeManager,
    strategy: NasdaqOrbM1BreakoutStrategy,
    symbol: str,
    timeframe: Timeframe,
    timeframe_str: str,
    lookback_days: int,
    kill_switch_flag_path: Path | None = None,
) -> None:
    bars_per_day = {"M1": 1440, "M5": 288, "M15": 96, "M30": 48, "H1": 24, "H4": 6}.get(timeframe_str, 1440)
    lookback_bars = lookback_days * bars_per_day
    bars = connector.fetch_recent_bars(symbol, timeframe_str, lookback_bars)
    logger.info("Fetched %d %s bar(s) for %s: %s -> %s", len(bars), timeframe_str, symbol, bars[0].timestamp, bars[-1].timestamp)

    mine, foreign = _partition_positions(broker.get_open_positions(), symbol)
    if len(mine) > 1:
        logger.error("Ambiguous open positions for %s (%d owned by this strategy); skipping.", symbol, len(mine))
        _log_trade_event("ambiguous_positions", symbol=symbol, count=len(mine))
        return
    if len(mine) == 1:
        _manage_open_trade(trade_manager, broker, mine[0], bars)
        return
    if foreign:
        logger.info("Skipping %s: %d position(s) held by another strategy.", symbol, len(foreign))
        _log_trade_event("foreign_position_blocks_entry", symbol=symbol, count=len(foreign))
        return

    _evaluate_for_new_trade(trade_manager, broker, strategy, bars, symbol, timeframe, kill_switch_flag_path)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    timeframe = Timeframe.M1

    global _CURRENT_MODE
    _CURRENT_MODE = "paper" if args.paper else "live"

    # Deliberately separate paper state/kill-switch files (see
    # run_live_sr_bias.py's identical per-symbol rationale) -- symbol-tagged
    # so a future second instance doesn't collide.
    symbol_tag = args.symbol.lower().replace(".", "_")
    risk_dir = Path(__file__).parent / "risk"
    kill_switch_flag_path = risk_dir / f"kill_switch_nasdaq_orb_{symbol_tag}_paper.flag" if args.paper else None
    daily_risk_tracker = (
        DailyRiskTracker(
            state_file=risk_dir / f"daily_risk_state_nasdaq_orb_{symbol_tag}_paper.json",
            kill_switch_flag_path=kill_switch_flag_path,
        )
        if args.paper
        else DailyRiskTracker()
    )

    if not args.paper:
        try:
            _ensure_explicit_demo_configuration()
        except DemoAccountRequiredError as exc:
            logger.critical("DEMO-ACCOUNT SAFETY RAIL TRIPPED (config): %s", exc)
            print(f"REFUSING TO START: {exc}")
            sys.exit(1)

    if is_trading_halted(kill_switch_flag_path):
        logger.info("RESULT: TRADING HALTED (kill-switch active)")
        print("TRADING HALTED (kill-switch active)")
        return

    connector = MT5Connector()
    broker = (
        PaperBroker(connector=connector, timeframe="M1", state_file=risk_dir / f"paper_broker_state_nasdaq_orb_{symbol_tag}.json")
        if args.paper
        else MT5Broker(connector=connector)
    )
    if not broker.connect():
        logger.error("Could not connect to MT5.")
        sys.exit(1)

    try:
        account_info = broker.get_account_info()
        if not args.paper:
            try:
                _ensure_demo_trade_mode(account_info)
            except DemoAccountRequiredError as exc:
                logger.critical("DEMO-ACCOUNT SAFETY RAIL TRIPPED (MT5 account): %s", exc)
                activate_kill_switch(f"run_live_nasdaq_orb.py: {exc}")
                print(f"REFUSING TO TRADE: {exc}")
                sys.exit(1)
            logger.info(
                "Demo-account safety rail passed: trade_mode=%s, currency=%s, equity=%.2f.",
                account_info.trade_mode, account_info.currency, account_info.equity,
            )
        else:
            logger.info("PAPER mode: balance=%.2f, equity=%.2f (no real orders will be placed).",
                        account_info.balance, account_info.equity)

        daily_risk_tracker.check_and_update(account_info.equity, account_info.login)

        strategy = NasdaqOrbM1BreakoutStrategy(
            config=NasdaqOrbM1BreakoutConfig(tp_r=args.tp_r, direction=args.direction)
        )
        position_sizer = PositionSizer(risk_per_trade_pct=args.risk_per_trade_pct)
        trade_manager = TradeManager(volume=args.volume, position_sizer=position_sizer)
        run_once(
            connector=connector, broker=broker, trade_manager=trade_manager, strategy=strategy,
            symbol=args.symbol, timeframe=timeframe, timeframe_str="M1",
            lookback_days=args.lookback_days, kill_switch_flag_path=kill_switch_flag_path,
        )
    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()
