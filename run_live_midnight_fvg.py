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

# Fast-poll: this script is normally re-invoked on a fixed cadence (e.g. every
# 2 minutes via Task Scheduler -- see run_live_midnight_fvg_demo.bat), which
# means a retest that closes just after one invocation can sit undetected for
# up to that whole interval. Since MT5Connector.fetch_recent_bars() only ever
# returns fully CLOSED bars (by design -- see its docstring -- to avoid
# signals off an incomplete candle), sub-minute polling can't see a NEWER bar
# than the M1 close cadence already provides; what it buys is catching a
# freshly-closed retest bar within seconds of it closing instead of within
# the next scheduled invocation. So: when a pending (found, not yet retested)
# FVG's near edge is within FAST_POLL_TRIGGER_POINTS of the latest close,
# this process stays alive and re-checks every FAST_POLL_INTERVAL_SECONDS,
# for up to FAST_POLL_MAX_SECONDS -- kept safely under the 2-minute scheduler
# cadence so this invocation always finishes before the next one starts
# (Task Scheduler's default MultipleInstances=IgnoreNew would otherwise skip
# the next tick outright rather than queuing it).
DEFAULT_FAST_POLL_TRIGGER_POINTS = 15.0
DEFAULT_FAST_POLL_INTERVAL_SECONDS = 20
DEFAULT_FAST_POLL_MAX_SECONDS = 90

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


# Every setup_id this script's strategy emits starts with this (see
# MidnightFvgStrategy.evaluate), and TradeManager.open_trade sends the
# setup_id as the order comment, so it travels back on the open Position.
# MT5Broker truncates long comments but preserves the first 20 characters
# (see _mt5_comment), which is more than this tag needs.
STRATEGY_TAG = "setup_midnight_fvg"


def _partition_positions(
    positions: list[Position], symbol: str, tag: str = STRATEGY_TAG
) -> tuple[list[Position], list[Position]]:
    """Splits this symbol's open positions into (ours, someone-else's).

    Several bots run against ONE account in this setup, and more than one of
    them trades NAS100 (this script on M1, run_live_sr_bias.py on M30).
    Filtering by symbol alone made them indistinguishable, which meant each
    bot would "manage" -- and could close -- a position the other opened,
    and two simultaneous positions made BOTH bots bail out with "ambiguous
    positions", leaving neither one managed (in paper mode that means SL/TP
    is never simulated at all).

    A position whose comment is empty or unrecognized is deliberately NOT
    counted as ours: ownership-unknown must never be treated as
    ownership-mine, or a hand-opened position would be closed by a bot.
    """
    same_symbol = [p for p in positions if p.symbol == symbol]
    mine = [p for p in same_symbol if p.comment.startswith(tag)]
    foreign = [p for p in same_symbol if not p.comment.startswith(tag)]
    return mine, foreign


def _manage_open_trade(trade_manager: TradeManager, broker: IBroker, position: Position, bars: list[Bar]) -> None:
    """Checks the open position's SL/TP against every bar closed SINCE it
    opened, not just the newest one.

    on_new_bar() only evaluates a single bar per call, and this invocation
    may be the first one to run after more than one new bar closed -- a
    routine multi-bar gap for M1 bars vs. the 2-minute poll cadence, or a
    much wider one after a Task Scheduler delay or PC sleep (see the
    documented sleep-gap incident). Checking only bars[-1] would let an
    SL/TP touch on an intermediate bar go completely undetected. This
    matters most in PAPER mode: PaperBroker has no real broker-side SL/TP
    order and relies entirely on this bar-by-bar check to simulate fills,
    so a skipped intermediate bar means a wrong/late paper outcome. Live/demo
    mode is unaffected either way -- MT5's own attached SL/TP order protects
    the real position in real time, independent of what this check sees.
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
    strategy: MidnightFvgStrategy,
    market_state: MarketState,
    symbol: str,
    timeframe_str: str,
    trigger_points: float,
    interval_seconds: float,
    max_seconds: float,
):
    """Stays alive re-checking for a retest close to a pending FVG instead of
    waiting for the next scheduled invocation -- see the fast-poll constants'
    module-level docstring for why this only shortens the detection lag
    (bar-close cadence is still ~1min) rather than adding tick-level
    granularity. Returns the TradeSetup if one fires during the window, else
    None (the pending FVG, if still valid, is picked up by the next scheduled
    run as before).
    """
    if trigger_points <= 0:
        return None
    if not strategy._fvg_found or strategy._trade_taken:
        return None

    latest = market_state.get_latest_bar()
    if latest is None:
        return None
    near_edge = strategy._fvg_upper if strategy._fvg_direction == SignalDirection.BUY else strategy._fvg_lower
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
            return None  # day rolled over, or another path already consumed the setup
        near_edge = strategy._fvg_upper if strategy._fvg_direction == SignalDirection.BUY else strategy._fvg_lower
        distance = abs(latest.close - near_edge)
        if distance > trigger_points:
            logger.info("Fast-poll: price moved back to %.1fpt away; falling back to normal cadence.", distance)
            return None

    logger.info("Fast-poll window elapsed with no retest; next scheduled invocation will pick it back up.")
    return None


def _evaluate_for_new_trade(
    trade_manager: TradeManager,
    broker: IBroker,
    strategy: MidnightFvgStrategy,
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
    """Replays every fetched bar through the strategy in chronological order
    (see module docstring for why this differs from
    run_live_accumulation_breakout.py's single evaluate() call), then acts
    only on the setup -- if any -- returned for the FINAL (newest) bar (or,
    failing that, a setup caught by the fast-poll extension below).

    Returns True only if an order was actually FILLED this call. This is
    deliberately NOT the same as strategy._trade_taken, which is set as soon
    as a setup is *proposed* -- a proposal can still be rejected by the
    broker or blocked by the kill-switch, in which case no trade exists and
    callers must not treat today as resolved (see run_once's daily-resolved
    cache, which relies on this return value for exactly that reason).
    """
    market_state = MarketState(symbol=symbol, timeframe=timeframe)
    setup = None
    for b in bars:
        market_state.append_bar(b)
        setup = strategy.evaluate(market_state)  # only the FINAL call's result (bars[-1]) is acted on below

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
    """Returns the NY calendar date this state file last marked fully
    resolved (no more signals possible today), or None if the file is
    missing/unreadable/doesn't mark a resolved day.
    """
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
    strategy: MidnightFvgStrategy,
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
    # Cheap skip: once today's NY session is fully resolved (a trade was
    # actually FILLED, or the session window closed with no matching FVG at
    # all), there is no new setup left this invocation could find until
    # tomorrow's session -- re-fetching and replaying ~4 days of M1 bars
    # through the strategy every 2 minutes for the rest of the day is pure
    # waste. But the open-position check itself must NEVER be skipped: a
    # filled trade still needs _manage_open_trade() called every invocation
    # (trailing/close-retry logic), so on a cache hit we still make the
    # cheap get_open_positions() + single-bar call instead of returning
    # outright.
    if daily_resolved_state_path is not None:
        resolved_date = _read_daily_resolved_date(daily_resolved_state_path)
        if resolved_date == datetime.now(NY).date():
            mine, _foreign = _partition_positions(broker.get_open_positions(), symbol)
            if len(mine) > 1:
                logger.error("Ambiguous open positions for %s (%d owned by this strategy); skipping.", symbol, len(mine))
                _log_trade_event("ambiguous_positions", symbol=symbol, count=len(mine))
                return
            if len(mine) == 1:
                # 60 bars (~1h of M1) is still cheap and comfortably covers a
                # routine multi-bar gap; see _manage_open_trade's docstring.
                recent_bars = connector.fetch_recent_bars(symbol, timeframe_str, 60)
                _manage_open_trade(trade_manager, broker, mine[0], recent_bars)
                return
            logger.info("RESULT: NO SIGNAL (cached: %s already resolved, skipping bar fetch)", resolved_date)
            _log_trade_event("no_signal_cached", symbol=symbol)
            return

    lookback_bars = lookback_days * 1440  # M1 bars/day upper bound; safe overestimate for weekends/gaps
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
        # Another bot (or a human) already has exposure on this symbol.
        # Deliberately conservative: adding our own position on top would
        # stack correlated risk AND margin on one account -- see the audit's
        # margin finding, where a single wide position can already consume
        # most of the account's free margin.
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

    if daily_resolved_state_path is not None and strategy._current_date is not None:
        # NOTE: deliberately NOT strategy._trade_taken -- that flag is set as
        # soon as a setup is *proposed*, before the broker fill/kill-switch
        # check, so it would wrongly mark a rejected/blocked day as "done"
        # and starve it of further retries (see _evaluate_for_new_trade's
        # docstring).
        today_done = filled or (
            not strategy._fvg_found and datetime.now(NY).date() == strategy._current_date
            and datetime.now(NY).time() >= strategy.config.session_end
        )
        _write_daily_resolved_state(daily_resolved_state_path, strategy._current_date, today_done)


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
    daily_resolved_state_path = risk_dir / (
        "midnight_fvg_daily_resolved_paper.json" if args.paper else "midnight_fvg_daily_resolved.json"
    )
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
            fast_poll_trigger_points=args.fast_poll_trigger_points,
            fast_poll_interval_seconds=args.fast_poll_interval_seconds,
            fast_poll_max_seconds=args.fast_poll_max_seconds,
            daily_resolved_state_path=daily_resolved_state_path,
        )
    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()
