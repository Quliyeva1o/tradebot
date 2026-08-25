#!/usr/bin/env python3
"""Live trading loop: Midnight FVG (00:00-00:30 NY) wired into
TradeManager/MT5Broker. Structural clone of run_live_accumulation_breakout.py
-- same two-layer demo-account safety rail, same kill-switch gate, same
paper-mode risk-state isolation -- with MidnightFvgStrategy swapped in.

SAFETY -- THIS SCRIPT PLACES REAL (DEMO-ACCOUNT) ORDERS UNLESS --paper IS
PASSED. See run_live_demo.py's module docstring for the full two-layer
demo-account enforcement description; it is reused UNCHANGED here
(_ensure_explicit_demo_configuration, _ensure_demo_trade_mode).

IMPORTANT -- one significant, deliberate DIFFERENCE from
run_live_accumulation_breakout.py's _evaluate_for_new_trade(), not a
cosmetic one: that script builds a MarketState from the full fetched
lookback and calls strategy.evaluate() EXACTLY ONCE (after appending every
bar), using only market_state.get_latest_bar() (the single newest bar).
Since main() constructs a FRESH strategy instance every invocation (this is
a one-shot script re-invoked by Task Scheduler; nothing persists between
runs), that pattern can never actually work for a strategy whose signal
needs multiple CONSECUTIVE ticks to build up internal state (e.g.
NyOpenAccumulationBreakoutStrategy's 2-8 candle accumulation window,
MidnightFvgStrategy's 3+ candle FVG-detection window) -- a fresh object
only ever gets ONE bar appended to its internal buffer before evaluate()
is called, so the "not ready yet" gate can never clear.
scripts/replay_live_strategy_check.py (the validation harness both bots
use) already calls evaluate() ONCE PER BAR inside its replay loop -- this
script follows THAT pattern instead: _evaluate_for_new_trade() below
replays every fetched bar through append_bar()+evaluate() in chronological
order (rebuilding the strategy's day-scoped state exactly as if it had been
running continuously), and only acts on the setup returned by the FINAL
(newest) bar's call -- a setup surfaced by an earlier bar in this same
fetch was already actionable on a prior invocation. This does NOT need
run_live_accumulation_breakout.py's 35-day lookback (MidnightFvgStrategy
needs no cross-timeframe DailyContext at all -- see its module docstring)
so the per-invocation replay cost stays small (~a few thousand bars, not
~50k). Flagged for the user: run_live_accumulation_breakout.py likely has
the same "can never fire" bug and has never been run to notice it (see
task.md FAZA 7) -- worth fixing there too, separately from this file.

IMPORTANT -- like NyOpenAccumulationBreakoutStrategy, this strategy is
backtested (scripts/first_fvg_backtest.py, see BACKTEST_FINDINGS.md
section 2.3) over 409 trades/~4.1yr with two multi-year stretches
(2023-2024) that were flat-to-breakeven and the most recent month
underwater in every tested variant. Run this in PAPER mode (see
config/execution_config.py / PaperBroker) for an extended period before
ever pointing --volume/--risk-per-trade-pct at anything real, even a demo
account's play money -- see MIDNIGHT_FVG_BOT_SPEC.md section 5.

Usage:
    python run_live_midnight_fvg.py --symbol NAS100 --timeframe M1 --volume 0.1 --paper
"""

import argparse
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.append(str(Path(__file__).parent.resolve()))

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
from strategy.midnight_fvg import MidnightFvgConfig, MidnightFvgStrategy
from utils.logging import setup_logger, setup_structured_logger
from market_structure.structure_models import MarketState

logger = setup_logger("run_live_midnight_fvg", log_to_file=True)
trade_events_logger = setup_structured_logger("trade_events")

NY = ZoneInfo("America/New_York")

# Only needs to cover the cross-midnight tail + a weekend gap (see
# MidnightFvgStrategy's "Cross-midnight FVG edge case" docstring) -- NOT
# run_live_accumulation_breakout.py's 35 days, since this strategy has no
# cross-timeframe DailyContext to warm up. 4 days safely spans a Mon 00:00
# session needing Fri's tail across a weekend.
DEFAULT_LOOKBACK_DAYS = 4
DEFAULT_VOLUME = 0.1

# Set once per process, at the top of main(), before anything else runs --
# see run_live_accumulation_breakout.py's identical global for the
# single-process-per-invocation rationale.
_CURRENT_MODE = "live"


class DemoAccountRequiredError(RuntimeError):
    """Raised when this script is not explicitly and verifiably pointed at a demo account."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Live Midnight FVG (00:00-00:30 NY) loop against a DEMO MT5 "
        "account (places real demo orders -- see module docstring)."
    )
    parser.add_argument("--symbol", default="NAS100")
    parser.add_argument("--timeframe", default="M1")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--volume", type=float, default=DEFAULT_VOLUME)
    parser.add_argument("--risk-per-trade-pct", type=float, default=None)
    parser.add_argument("--fixed-tp-r", type=float, default=2.5)
    parser.add_argument("--min-gap-points", type=float, default=3.0)
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Use PaperBroker (virtual fills against real MT5 prices, no real orders) instead of "
        "MT5Broker. RECOMMENDED for an extended trial before ever running without this flag -- "
        "see module docstring on why (two multi-year flat stretches in the backtest sample).",
    )
    return parser.parse_args(argv)


def _ensure_explicit_demo_configuration() -> None:
    """Identical gate to run_live_demo.py -- see that module for the full rationale."""
    account_type = Settings.load().MT5_ACCOUNT_TYPE.strip().lower()
    if account_type != "demo":
        raise DemoAccountRequiredError(
            f"MT5_ACCOUNT_TYPE must be explicitly set to 'demo' in .env to run "
            f"run_live_midnight_fvg.py (got {account_type!r}). Refusing to start."
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


def _manage_open_trade(trade_manager: TradeManager, broker: IBroker, position: Position, final_bar: Bar) -> None:
    symbol = position.symbol
    if position.stop_loss is None or position.take_profit is None:
        logger.error("Open position %s for %s has no stop_loss/take_profit; cannot manage.", position.id, symbol)
        _log_trade_event("unmanageable_position", symbol=symbol, position_id=position.id)
        return

    _attach_to_open_position(trade_manager, broker, position)
    action = trade_manager.on_new_bar(final_bar)

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
    strategy: MidnightFvgStrategy,
    bars: list[Bar],
    symbol: str,
    timeframe: Timeframe,
    kill_switch_flag_path: Path | None = None,
) -> None:
    """Replays every fetched bar through the strategy in chronological order
    (see module docstring for why this differs from
    run_live_accumulation_breakout.py's single evaluate() call), then acts
    only on the setup -- if any -- returned for the FINAL (newest) bar.
    """
    market_state = MarketState(symbol=symbol, timeframe=timeframe)
    setup = None
    for b in bars:
        market_state.append_bar(b)
        setup = strategy.evaluate(market_state)  # only the FINAL call's result (bars[-1]) is acted on below

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
    strategy: MidnightFvgStrategy,
    symbol: str,
    timeframe: Timeframe,
    timeframe_str: str,
    lookback_days: int,
    kill_switch_flag_path: Path | None = None,
) -> None:
    lookback_bars = lookback_days * 1440  # M1 bars/day upper bound; safe overestimate for weekends/gaps
    bars = connector.fetch_recent_bars(symbol, timeframe_str, lookback_bars)
    logger.info("Fetched %d bar(s) for %s [%s]: %s -> %s", len(bars), symbol, timeframe_str, bars[0].timestamp, bars[-1].timestamp)

    open_positions = [p for p in broker.get_open_positions() if p.symbol == symbol]
    if len(open_positions) > 1:
        logger.error("Ambiguous open positions for %s (%d found); skipping.", symbol, len(open_positions))
        _log_trade_event("ambiguous_positions", symbol=symbol, count=len(open_positions))
        return
    if len(open_positions) == 1:
        _manage_open_trade(trade_manager, broker, open_positions[0], bars[-1])
        return

    _evaluate_for_new_trade(trade_manager, broker, strategy, bars, symbol, timeframe, kill_switch_flag_path)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    timeframe = Timeframe[args.timeframe]

    global _CURRENT_MODE
    _CURRENT_MODE = "paper" if args.paper else "live"

    # --paper's virtual balance is not comparable to the real (demo-account)
    # equity, so its daily-loss baseline and kill-switch flag are kept in
    # separate files -- see run_live_accumulation_breakout.py's identical
    # rationale. Deliberately a DIFFERENT paper state file than that
    # script's (daily_risk_state_paper.json/kill_switch_paper.flag) so the
    # two bots' paper runs, if ever run in parallel against the same demo
    # account, don't corrupt or halt each other's virtual baseline either.
    risk_dir = Path(__file__).parent / "risk"
    kill_switch_flag_path = risk_dir / "kill_switch_midnight_fvg_paper.flag" if args.paper else None
    daily_risk_tracker = (
        DailyRiskTracker(
            state_file=risk_dir / "daily_risk_state_midnight_fvg_paper.json",
            kill_switch_flag_path=kill_switch_flag_path,
        )
        if args.paper
        else DailyRiskTracker()
    )

    # PaperBroker never sends a real order regardless of which MT5 account
    # is connected -- see run_live_accumulation_breakout.py's identical
    # rationale for skipping the demo-account config rail for --paper only.
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
    broker = PaperBroker(connector=connector, timeframe=args.timeframe) if args.paper else MT5Broker(connector=connector)
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
                activate_kill_switch(f"run_live_midnight_fvg.py: {exc}")
                print(f"REFUSING TO TRADE: {exc}")
                sys.exit(1)
            logger.info(
                "Demo-account safety rail passed: trade_mode=%s, currency=%s, equity=%.2f.",
                account_info.trade_mode, account_info.currency, account_info.equity,
            )
        else:
            logger.info("PAPER mode: balance=%.2f, equity=%.2f (no real orders will be placed).",
                        account_info.balance, account_info.equity)

        daily_risk_tracker.check_and_update(account_info.equity)

        strategy = MidnightFvgStrategy(
            config=MidnightFvgConfig(fixed_tp_r=args.fixed_tp_r, min_gap_points=args.min_gap_points)
        )
        position_sizer = (
            PositionSizer(risk_per_trade_pct=args.risk_per_trade_pct)
            if args.risk_per_trade_pct is not None
            else None
        )
        trade_manager = TradeManager(volume=args.volume, position_sizer=position_sizer)
        run_once(
            connector=connector, broker=broker, trade_manager=trade_manager, strategy=strategy,
            symbol=args.symbol, timeframe=timeframe, timeframe_str=args.timeframe,
            lookback_days=args.lookback_days, kill_switch_flag_path=kill_switch_flag_path,
        )
    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()
