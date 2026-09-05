#!/usr/bin/env python3
"""Live trading loop: First FVG (09:30 NY + 15m + fixed 2R) wired into
TradeManager/MT5Broker. Structural clone of run_live_midnight_fvg.py -- same
two-layer demo-account safety rail, same kill-switch gate, same paper-mode
risk-state isolation, same per-bar-replay pattern for a fresh-per-invocation
strategy instance -- with FirstFvg15mStrategy swapped in.

This is the NEW, validated-with-spread configuration
(FIRST_FVG_15M_SPREAD_REPORT.md: PF 1.01/5y, 1.16/1y, n=1001/198 -- the only
First FVG variant that survived real spread on both large-sample windows).
It REPLACES run_live_midnight_fvg.py (00:00 session, M1, fixed 2.5R), which
was confirmed net-negative with spread on every tested window
(FIRST_FVG_15M_SPREAD_REPORT.md section 8 update) and has been disabled in
Task Scheduler.

SAFETY -- THIS SCRIPT PLACES REAL (DEMO-ACCOUNT) ORDERS UNLESS --paper IS
PASSED. See run_live_demo.py's module docstring for the full two-layer
demo-account enforcement description; it is reused UNCHANGED here.

Per-bar replay: FirstFvg15mStrategy's session-scoped state (has today's FVG
been found yet? has today's one trade already been taken?) needs to be
rebuilt from every bar in today's session, not just the newest one -- same
reasoning as run_live_midnight_fvg.py's identical docstring section, and
verified against scripts/first_fvg_15m_spread_backtest.py's run_session()
by replaying the full 6-year NAS100 M15 history through both and diffing:
1116/1116 trades match exactly (entry price and stop, to the cent).

No fast-cadence concern here unlike the M1 midnight variant: this strategy
trades M15 bars against a ~2-minute poll, so a bar gets roughly 7-8 chances
to be "the newest bar" during its own 15-minute life -- the M1-vs-2-minute-
poll gap that could silently skip an entire bar (see run_live_midnight_fvg.py's
module docstring) does not apply here. The fast-poll extension below is kept
only for extra responsiveness (catching a retest within seconds instead of
within the next ~2-minute tick), not because it's needed for correctness.

No cross-session daily-resolved-cache "window closed" shortcut like
run_live_midnight_fvg.py's: that strategy's FVG-detection window closes at
a fixed clock time (00:30), so "no FVG found by then" is a permanently dead
day. This strategy's window is "09:30 until NY midnight rollover" -- there
is no earlier fixed time at which the day can be known dead, so the
resolved-cache here is written ONLY once a trade is actually filled (every
other invocation re-does the (cheap, M15-scale) bar fetch and replay).

Usage:
    python run_live_first_fvg_15m.py --symbol NAS100 --timeframe M15 --paper
"""

import argparse
import json
import sys
import time as time_module
from datetime import date, datetime
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
from strategy.first_fvg_15m import FirstFvg15mConfig, FirstFvg15mStrategy
from utils.logging import setup_logger, setup_structured_logger
from market_structure.structure_models import MarketState

logger = setup_logger("run_live_first_fvg_15m", log_to_file=True)
trade_events_logger = setup_structured_logger("trade_events")

NY = ZoneInfo("America/New_York")

DEFAULT_LOOKBACK_DAYS = 4
DEFAULT_VOLUME = 0.1

DEFAULT_FAST_POLL_TRIGGER_POINTS = 15.0
DEFAULT_FAST_POLL_INTERVAL_SECONDS = 20
DEFAULT_FAST_POLL_MAX_SECONDS = 90

_CURRENT_MODE = "live"


class DemoAccountRequiredError(RuntimeError):
    """Raised when this script is not explicitly and verifiably pointed at a demo account."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Live First FVG (09:30 NY + 15m + fixed 2R) loop against a DEMO MT5 "
        "account (places real demo orders -- see module docstring)."
    )
    parser.add_argument("--symbol", default="NAS100")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--volume", type=float, default=DEFAULT_VOLUME)
    parser.add_argument("--risk-per-trade-pct", type=float, default=None)
    parser.add_argument(
        "--require-ranging-regime", action="store_true",
        help=(
            "Opt-in gate from ADVANCED_VALIDATION_REPORT.md #3/#3.1: only take a trade if "
            "research.regime_analysis.analyze_regime() classifies the trailing 200 M15 bars as "
            "RANGING. Improved 8/10 walk-forward folds in backtest and is regression-free on the "
            "live class when off (the default) -- but has not yet been forward/paper-validated, "
            "so it defaults to off."
        ),
    )
    parser.add_argument("--fixed-tp-r", type=float, default=2.0,
                         help="Default 2.0 -- the only R multiple validated with spread. "
                         "Do not set 3.0: tested and confirmed worse on every metric, "
                         "see FIRST_FVG_15M_SPREAD_REPORT.md section 3.")
    parser.add_argument(
        "--fast-poll-trigger-points",
        type=float,
        default=DEFAULT_FAST_POLL_TRIGGER_POINTS,
        help="When a pending unretested FVG's near edge is within this many points of the "
        "latest close, stay alive and re-check more often instead of waiting for the next "
        "scheduled invocation. Set to 0 to disable fast-polling entirely.",
    )
    parser.add_argument("--fast-poll-interval-seconds", type=float, default=DEFAULT_FAST_POLL_INTERVAL_SECONDS)
    parser.add_argument("--fast-poll-max-seconds", type=float, default=DEFAULT_FAST_POLL_MAX_SECONDS)
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Use PaperBroker (virtual fills against real MT5 prices, no real orders) instead of "
        "MT5Broker. RECOMMENDED before ever running without this flag.",
    )
    return parser.parse_args(argv)


def _ensure_explicit_demo_configuration() -> None:
    """Identical gate to run_live_demo.py -- see that module for the full rationale."""
    account_type = Settings.load().MT5_ACCOUNT_TYPE.strip().lower()
    if account_type != "demo":
        raise DemoAccountRequiredError(
            f"MT5_ACCOUNT_TYPE must be explicitly set to 'demo' in .env to run "
            f"run_live_first_fvg_15m.py (got {account_type!r}). Refusing to start."
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


# Every setup_id this script's strategy emits starts with this (see
# FirstFvg15mStrategy.evaluate), and TradeManager.open_trade sends the
# setup_id as the order comment, so it travels back on the open Position.
STRATEGY_TAG = "setup_first_fvg_15m"


def _partition_positions(
    positions: list[Position], symbol: str, tag: str = STRATEGY_TAG
) -> tuple[list[Position], list[Position]]:
    """Splits this symbol's open positions into (ours, someone-else's) --
    see run_live_midnight_fvg.py's identical function for the full
    multi-bot-on-one-account rationale (SESSION_HANDOFF.md #2.5).
    """
    same_symbol = [p for p in positions if p.symbol == symbol]
    mine = [p for p in same_symbol if p.comment.startswith(tag)]
    foreign = [p for p in same_symbol if not p.comment.startswith(tag)]
    return mine, foreign


def _manage_open_trade(trade_manager: TradeManager, broker: IBroker, position: Position, bars: list[Bar]) -> None:
    """Checks the open position's SL/TP against every bar closed SINCE it
    opened, not just the newest one -- see run_live_midnight_fvg.py's
    identical function docstring (SESSION_HANDOFF.md #2.4).
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


def _fast_poll_for_retest(
    connector: MT5Connector,
    strategy: FirstFvg15mStrategy,
    market_state: MarketState,
    symbol: str,
    timeframe_str: str,
    trigger_points: float,
    interval_seconds: float,
    max_seconds: float,
):
    """Stays alive re-checking for a retest close to a pending FVG instead of
    waiting for the next scheduled invocation -- see
    run_live_midnight_fvg.py's identical function; module docstring here
    explains why this is a nice-to-have, not a correctness requirement, for
    an M15 strategy.
    """
    if trigger_points <= 0:
        return None
    if not strategy._fvg_found or strategy._trade_taken:
        return None

    latest = market_state.get_latest_bar()
    if latest is None:
        return None
    near_edge = strategy._zone_top if strategy._fvg_direction == SignalDirection.BUY else strategy._zone_bottom
    distance = abs(latest.close - near_edge)
    if distance > trigger_points:
        return None

    logger.info(
        "Pending %s FVG near edge %.2f is %.1fpt from last close %.2f (<= %.1fpt trigger) -- "
        "fast-polling every %.0fs for up to %.0fs.",
        strategy._fvg_direction.name, near_edge, distance, latest.close, trigger_points,
        interval_seconds, max_seconds,
    )

    elapsed = 0.0
    while elapsed < max_seconds:
        time_module.sleep(interval_seconds)
        elapsed += interval_seconds

        fresh_bars = connector.fetch_recent_bars(symbol, timeframe_str, 5)
        new_bars = [b for b in fresh_bars if b.timestamp > latest.timestamp]
        setup = None
        for b in new_bars:
            market_state.append_bar(b)
            setup = strategy.evaluate(market_state)
            latest = b
        if new_bars:
            logger.info("Fast-poll: %d new closed bar(s) up to %s.", len(new_bars), latest.timestamp)
        if setup is not None:
            logger.info("Fast-poll caught the retest %.0fs after it became this close.", elapsed)
            return setup
        if not strategy._fvg_found or strategy._trade_taken:
            return None
        near_edge = strategy._zone_top if strategy._fvg_direction == SignalDirection.BUY else strategy._zone_bottom
        distance = abs(latest.close - near_edge)
        if distance > trigger_points:
            logger.info("Fast-poll: price moved back to %.1fpt away; falling back to normal cadence.", distance)
            return None

    logger.info("Fast-poll window elapsed with no retest; next scheduled invocation will pick it back up.")
    return None


def _evaluate_for_new_trade(
    trade_manager: TradeManager,
    broker: IBroker,
    strategy: FirstFvg15mStrategy,
    bars: list[Bar],
    symbol: str,
    timeframe: Timeframe,
    kill_switch_flag_path: Path | None = None,
    connector: MT5Connector | None = None,
    timeframe_str: str | None = None,
    fast_poll_trigger_points: float = 0.0,
    fast_poll_interval_seconds: float = DEFAULT_FAST_POLL_INTERVAL_SECONDS,
    fast_poll_max_seconds: float = DEFAULT_FAST_POLL_MAX_SECONDS,
) -> bool:
    """Replays every fetched bar through the strategy in chronological order,
    then acts only on the setup -- if any -- returned for the FINAL (newest)
    bar's call (or, failing that, a setup caught by the fast-poll extension
    below). See run_live_midnight_fvg.py's identical function docstring.
    """
    market_state = MarketState(symbol=symbol, timeframe=timeframe)
    setup = None
    for b in bars:
        market_state.append_bar(b)
        setup = strategy.evaluate(market_state)

    if setup is None and connector is not None and timeframe_str is not None:
        setup = _fast_poll_for_retest(
            connector, strategy, market_state, symbol, timeframe_str,
            fast_poll_trigger_points, fast_poll_interval_seconds, fast_poll_max_seconds,
        )

    if setup is None:
        reasons = top_rejection_reasons({"strategy": strategy.diagnostics.summary()})
        reasons_str = ", ".join(f"{r} ({c})" for r, c in reasons) if reasons else "no diagnostics recorded"
        logger.info("RESULT: NO SIGNAL (top reason: %s)", reasons_str)
        _log_trade_event("no_signal", symbol=symbol)
        return False

    logger.info("RESULT: SIGNAL %s %s @ %s", setup.symbol, setup.direction.name, setup.timestamp)
    _log_trade_event("signal_found", symbol=symbol, direction=setup.direction.name, setup_id=setup.setup_id)

    if is_trading_halted(kill_switch_flag_path):
        logger.warning("Signal found for %s but kill-switch is active; refusing to open.", symbol)
        _log_trade_event("signal_blocked_kill_switch", symbol=symbol, setup_id=setup.setup_id)
        return False

    order = trade_manager.open_trade(setup, broker)
    if order.status is OrderStatus.FILLED:
        assert order.fill_price is not None
        logger.info("Trade opened for %s: order_id=%s fill_price=%.5f", symbol, order.order_id, order.fill_price)
        _log_trade_event("trade_opened", symbol=symbol, setup_id=setup.setup_id, order_id=order.order_id, fill_price=order.fill_price)
        return True
    else:
        open_result = trade_manager.last_open_result
        reason = open_result.comment if open_result is not None else "unknown"
        retcode = open_result.retcode if open_result is not None else None
        logger.error("Trade open REJECTED for %s: reason=%s (retcode=%s)", symbol, reason, retcode)
        _log_trade_event("trade_open_rejected", symbol=symbol, setup_id=setup.setup_id, order_id=order.order_id, reason=reason, retcode=retcode)
        return False


def _read_daily_resolved_date(state_path: Path) -> date | None:
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not data.get("resolved"):
        return None
    try:
        return date.fromisoformat(data["ny_date"])
    except (KeyError, ValueError):
        return None


def _write_daily_resolved_state(state_path: Path, ny_date: date, resolved: bool) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"ny_date": ny_date.isoformat(), "resolved": resolved}), encoding="utf-8")


def run_once(
    connector: MT5Connector,
    broker: IBroker,
    trade_manager: TradeManager,
    strategy: FirstFvg15mStrategy,
    symbol: str,
    timeframe: Timeframe,
    timeframe_str: str,
    lookback_days: int,
    kill_switch_flag_path: Path | None = None,
    fast_poll_trigger_points: float = 0.0,
    fast_poll_interval_seconds: float = DEFAULT_FAST_POLL_INTERVAL_SECONDS,
    fast_poll_max_seconds: float = DEFAULT_FAST_POLL_MAX_SECONDS,
    daily_resolved_state_path: Path | None = None,
) -> None:
    # See module docstring: unlike run_live_midnight_fvg.py, there is no
    # fixed clock time at which "no FVG yet" becomes a permanently dead day
    # for this strategy (its window runs 09:30 -> NY midnight rollover), so
    # the resolved-cache is only ever written True once a trade is actually
    # FILLED -- never on "no signal yet", to avoid wrongly starving a day
    # that could still produce a retest later.
    if daily_resolved_state_path is not None:
        resolved_date = _read_daily_resolved_date(daily_resolved_state_path)
        if resolved_date == datetime.now(NY).date():
            mine, _foreign = _partition_positions(broker.get_open_positions(), symbol)
            if len(mine) > 1:
                logger.error("Ambiguous open positions for %s (%d owned by this strategy); skipping.", symbol, len(mine))
                _log_trade_event("ambiguous_positions", symbol=symbol, count=len(mine))
                return
            if len(mine) == 1:
                recent_bars = connector.fetch_recent_bars(symbol, timeframe_str, 60)
                _manage_open_trade(trade_manager, broker, mine[0], recent_bars)
                return
            logger.info("RESULT: NO SIGNAL (cached: %s already resolved, skipping bar fetch)", resolved_date)
            _log_trade_event("no_signal_cached", symbol=symbol)
            return

    lookback_bars = lookback_days * 96  # M15 bars/day upper bound (96 = 24h*4); safe overestimate for weekends/gaps
    bars = connector.fetch_recent_bars(symbol, timeframe_str, lookback_bars)
    logger.info("Fetched %d bar(s) for %s [%s]: %s -> %s", len(bars), symbol, timeframe_str, bars[0].timestamp, bars[-1].timestamp)

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

    filled = _evaluate_for_new_trade(
        trade_manager, broker, strategy, bars, symbol, timeframe, kill_switch_flag_path,
        connector=connector, timeframe_str=timeframe_str,
        fast_poll_trigger_points=fast_poll_trigger_points,
        fast_poll_interval_seconds=fast_poll_interval_seconds,
        fast_poll_max_seconds=fast_poll_max_seconds,
    )

    if daily_resolved_state_path is not None and strategy._current_date is not None and filled:
        _write_daily_resolved_state(daily_resolved_state_path, strategy._current_date, True)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    timeframe = Timeframe[args.timeframe]

    global _CURRENT_MODE
    _CURRENT_MODE = "paper" if args.paper else "live"

    risk_dir = Path(__file__).parent / "risk"
    kill_switch_flag_path = risk_dir / "kill_switch_first_fvg_15m_paper.flag" if args.paper else None
    daily_resolved_state_path = risk_dir / (
        "first_fvg_15m_daily_resolved_paper.json" if args.paper else "first_fvg_15m_daily_resolved.json"
    )
    daily_risk_tracker = (
        DailyRiskTracker(
            state_file=risk_dir / "daily_risk_state_first_fvg_15m_paper.json",
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
        PaperBroker(connector=connector, timeframe=args.timeframe, state_file=risk_dir / "paper_broker_state_first_fvg_15m.json")
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
                activate_kill_switch(f"run_live_first_fvg_15m.py: {exc}")
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

        strategy = FirstFvg15mStrategy(config=FirstFvg15mConfig(
            fixed_tp_r=args.fixed_tp_r, require_ranging_regime=args.require_ranging_regime,
        ))
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
            fast_poll_trigger_points=args.fast_poll_trigger_points,
            fast_poll_interval_seconds=args.fast_poll_interval_seconds,
            fast_poll_max_seconds=args.fast_poll_max_seconds,
            daily_resolved_state_path=daily_resolved_state_path,
        )
    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()
