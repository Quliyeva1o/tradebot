#!/usr/bin/env python3
"""Live trading loop: NY-Open Accumulation Breakout+Retest wired into
TradeManager/MT5Broker. Structural clone of run_live_demo.py -- same
two-layer demo-account safety rail, same kill-switch gate, same
one-tick-per-invocation convention (re-invoked by Task Scheduler; no
loop/sleep here) -- with NasdaqMidlineSweepStrategy swapped for
NyOpenAccumulationBreakoutStrategy and one extra per-tick step: this
strategy also needs a daily HTF-structure bias / PDH-PDL / session-liquidity
context that a single M1 MarketState can't supply on its own (see
strategy/ny_open_accumulation_breakout.py's module docstring) -- so each
tick, BEFORE evaluating, this script also computes and pushes that day's
DailyContext via compute_daily_context()/set_daily_context(), reusing the
SAME M1 bar fetch already pulled for the strategy itself (no extra D1 fetch).

SAFETY -- THIS SCRIPT PLACES REAL (DEMO-ACCOUNT) ORDERS, same as
run_live_demo.py. See that script's module docstring for the full two-layer
demo-account enforcement description; it is reused UNCHANGED here
(_ensure_explicit_demo_configuration, _ensure_demo_trade_mode).

IMPORTANT -- this strategy has only been backtested (see
scripts/accumulation_breakout_backtest.py and the conversation that shaped
it) over a modest number of trades (13-91 depending on the test window) with
substantial month-to-month variance -- several tested windows had one or two
strong months carrying most of the profit. Run this in PAPER mode (see
config/execution_config.py / PaperBroker) for an extended period before ever
pointing --volume/--risk-per-trade-pct at anything real, even a demo
account's play money.

Usage:
    python run_live_accumulation_breakout.py --symbol USTEC --timeframe M1 --volume 0.1
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
from strategy.ny_open_accumulation_breakout import (
    NyOpenAccumulationBreakoutConfig,
    NyOpenAccumulationBreakoutStrategy,
    compute_daily_context,
)
from utils.logging import setup_logger, setup_structured_logger
from market_structure.structure_models import MarketState

logger = setup_logger("run_live_accumulation_breakout", log_to_file=True)
trade_events_logger = setup_structured_logger("trade_events")

DEFAULT_LOOKBACK_DAYS = 35  # covers structure_lookback_days(20) + swing_lookback_days(15) + buffer
DEFAULT_VOLUME = 0.1

# Set once per process, at the top of main(), before anything else runs --
# each invocation is a single short-lived one-shot (re-invoked by Task
# Scheduler, no loop here), so a module global is safe: there is no
# concurrent second "mode" within one process. Read by _log_trade_event()
# so a shared trade_events.log can distinguish paper-mode entries from
# real/live ones when both are scheduled in parallel against the same
# strategy (see module docstring).
_CURRENT_MODE = "live"


class DemoAccountRequiredError(RuntimeError):
    """Raised when this script is not explicitly and verifiably pointed at a demo account."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Live NY-Open Accumulation Breakout+Retest loop against a DEMO MT5 "
        "account (places real demo orders -- see module docstring)."
    )
    parser.add_argument("--symbol", default="USTEC")
    parser.add_argument("--timeframe", default="M1")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--volume", type=float, default=DEFAULT_VOLUME)
    parser.add_argument("--risk-per-trade-pct", type=float, default=None)
    parser.add_argument("--max-rr-cap", type=float, default=3.0)
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Use PaperBroker (virtual fills against real MT5 prices, no real orders) instead of "
        "MT5Broker. RECOMMENDED for an extended trial before ever running without this flag -- "
        "see module docstring on why (thin/variable backtest sample).",
    )
    return parser.parse_args(argv)


def _ensure_explicit_demo_configuration() -> None:
    """Identical gate to run_live_demo.py -- see that module for the full rationale."""
    account_type = Settings.load().MT5_ACCOUNT_TYPE.strip().lower()
    if account_type != "demo":
        raise DemoAccountRequiredError(
            f"MT5_ACCOUNT_TYPE must be explicitly set to 'demo' in .env to run "
            f"run_live_accumulation_breakout.py (got {account_type!r}). Refusing to start."
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
    strategy: NyOpenAccumulationBreakoutStrategy,
    bars: list[Bar],
    symbol: str,
    timeframe: Timeframe,
    kill_switch_flag_path: Path | None = None,
) -> None:
    market_state = MarketState(symbol=symbol, timeframe=timeframe)
    for b in bars:
        market_state.append_bar(b)

    for_date = bars[-1].timestamp.astimezone(ZoneInfo("America/New_York")).date()
    ctx = compute_daily_context(bars=bars, for_date=for_date)
    if ctx is not None:
        strategy.set_daily_context(ctx)
    else:
        logger.info("No DailyContext available yet for %s (insufficient history) -- no trade possible today.", for_date)

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
    strategy: NyOpenAccumulationBreakoutStrategy,
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

    _evaluate_for_new_trade(
        trade_manager, broker, strategy, bars, symbol, timeframe, kill_switch_flag_path
    )


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    timeframe = Timeframe[args.timeframe]

    global _CURRENT_MODE
    _CURRENT_MODE = "paper" if args.paper else "live"

    # --paper's virtual balance is not comparable to the real (demo-account)
    # equity, so its daily-loss baseline and kill-switch flag are kept in
    # separate files -- otherwise running both modes in parallel (paper +
    # real, on the same demo account, e.g. to validate this strategy live)
    # would let one mode's equity swings corrupt the other's baseline, or
    # spuriously halt the other's trading. Real/live mode is left on the
    # module defaults (risk/daily_risk_state.json, risk/kill_switch.flag) --
    # unchanged from before this parameter existed, and deliberately shared
    # with run_live_demo.py/any other real-account script, since that is
    # one real account with one real daily-loss limit.
    risk_dir = Path(__file__).parent / "risk"
    kill_switch_flag_path = risk_dir / "kill_switch_paper.flag" if args.paper else None
    daily_risk_tracker = (
        DailyRiskTracker(
            state_file=risk_dir / "daily_risk_state_paper.json",
            kill_switch_flag_path=kill_switch_flag_path,
        )
        if args.paper
        else DailyRiskTracker()
    )

    # PaperBroker never sends a real order regardless of which MT5 account
    # is connected (it only reads prices) -- the demo-account safety rail
    # below exists to gate REAL order placement, so it's irrelevant friction
    # for --paper and is skipped for that path only. Real-order paths
    # (MT5Broker) still go through both layers, unconditionally.
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
                activate_kill_switch(f"run_live_accumulation_breakout.py: {exc}")
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

        strategy = NyOpenAccumulationBreakoutStrategy(
            config=NyOpenAccumulationBreakoutConfig(max_rr_cap=args.max_rr_cap)
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
