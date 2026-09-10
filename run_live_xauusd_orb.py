#!/usr/bin/env python3
"""Live trading loop: XAUUSD 09:30 ORB Liquidity-Sweep strategy (Setup B
only -- see strategy/xauusd_orb_liquidity_sweep.py's module docstring for
the full validation) wired into TradeManager/MT5Broker -- structural clone
of run_live_sr_bias.py's simpler (no fast-poll, no second D1 feed) pattern,
with XauusdOrbLiquiditySweepStrategy swapped in.

SAFETY -- THIS SCRIPT PLACES REAL (DEMO-ACCOUNT) ORDERS UNLESS --paper IS
PASSED. See run_live_demo.py's module docstring for the full two-layer
demo-account enforcement description; reused unchanged here.

NOT YET forward/paper-validated -- unlike run_live_first_fvg_15m.py and
run_live_sr_bias.py (both already fidelity-checked against months of real
order flow), this is a fresh backtest-to-live port. Run with --paper for an
extended trial BEFORE ever running without that flag -- there is no
established live track record to fall back on yet.

Symbol name note: this account's broker (FXTM-Demo02) lists gold as plain
"XAUUSD" -- the earlier "XAUUSD.ifx" default was specific to a DIFFERENT
account (IFXBrokers) used only for that session's initial research; always
check --symbol against mt5.symbols_get() before running on any other
account/broker rather than assuming the bare ticker resolves.

No session-window gate beyond what the strategy itself enforces (09:30-11:00
NY, day-scoped, since the 2026-09-01 M15 port -- see
strategy/xauusd_orb_liquidity_sweep.py's module docstring) and no fast-poll
extension: the strategy's own entry window is 75 minutes wide on M15 bars (5
bars), so a ~2-minute poll cadence already gives each bar many chances to be
"the newest bar" during its life -- adequate without sub-bar polling, same
reasoning run_live_sr_bias.py gives for its own bar-close-triggered entries.

Usage:
    python run_live_xauusd_orb.py --symbol XAUUSD --timeframe M15 --paper
"""

import argparse
import sys
from dataclasses import replace
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

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
from strategy.xauusd_orb_liquidity_sweep import XauusdOrbLiquiditySweepConfig, XauusdOrbLiquiditySweepStrategy
from utils.logging import setup_logger, setup_structured_logger
from market_structure.structure_models import MarketState

NY = ZoneInfo("America/New_York")

logger = setup_logger("run_live_xauusd_orb", log_to_file=True)
trade_events_logger = setup_structured_logger("trade_events")

# M15 default: 3 days gives ample ATR(14) warmup (needs >3.5h of M15 bars)
# plus today's full session, at a trivial per-invocation replay size
# (3 * 96 M15 bars/day = 288).
DEFAULT_LOOKBACK_DAYS = 3
DEFAULT_VOLUME = 0.1
DEFAULT_RISK_PER_TRADE_PCT = 0.005  # 0.5% -- the strategy's own validated default, see module docstring

# See run_live_nasdaq_orb.py's SIGNAL_GRACE_MINUTES for the full account of the
# bug this closes. Same shape here: XauusdOrbLiquiditySweepStrategy latches
# `_trades_today` on the entry bar, so the setup existed for exactly one bar and
# a poll whose newest bar was not that bar could never see it. These bots run
# M15, where one bar spans seven polls, so the phase lock that cost the M1 bots
# half their signals does not bite -- but a single missed or slow poll still
# loses the day, and there is no reason to leave that open.
SIGNAL_GRACE_MINUTES = 4


def _grace_bars(timeframe_str: str) -> int:
    """SIGNAL_GRACE_MINUTES converted to this timeframe's bar count."""
    per_bar = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60}.get(timeframe_str, 15)
    return max(1, SIGNAL_GRACE_MINUTES // per_bar)

_CURRENT_MODE = "live"


class DemoAccountRequiredError(RuntimeError):
    """Raised when this script is not explicitly and verifiably pointed at a demo account."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Live XAUUSD 09:30 ORB Liquidity-Sweep loop against a DEMO MT5 account "
        "(places real demo orders -- see module docstring)."
    )
    parser.add_argument("--symbol", default="XAUUSD", help="MT5 symbol name (this account's ticker, see module docstring)")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument(
        "--entry-window-end", default=None, metavar="HH:MM",
        help="NY local time after which no NEW setup may start. Defaults to "
             "XauusdOrbLiquiditySweepConfig's own 11:00. The 2026-09-09 sweep found "
             "12:00 stronger on XAUUSD (PF 1.365 vs 1.246) and JP225 (1y PF 1.854 vs "
             "1.578) by allowing setups an extra hour to form; it also roughly doubles "
             "the drawdown on JP225, so it is opt-in rather than the new default.",
    )
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--volume", type=float, default=DEFAULT_VOLUME)
    parser.add_argument("--risk-per-trade-pct", type=float, default=DEFAULT_RISK_PER_TRADE_PCT)
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
            f"run_live_xauusd_orb.py (got {account_type!r}). Refusing to start."
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


# Every setup_id XauusdOrbLiquiditySweepStrategy emits starts with this (see
# _build_setup in strategy/xauusd_orb_liquidity_sweep.py), and
# TradeManager.open_trade sends the setup_id as the order comment, so it
# travels back on the open Position.
#
# MUST be <=20 chars: MT5Broker._mt5_comment() truncates any comment over
# _MT5_COMMENT_MAX_LENGTH=29 to its first 20 chars + "_" + an 8-hex-char
# hash (execution/mt5_broker.py). The original "setup_xauusd_orb_reversal"
# (25 chars) was silently broken -- Position.comment.startswith(STRATEGY_TAG)
# was ALWAYS False for any real (non-Paper) fill, since the stored comment
# only ever contains the first 20 characters. Never caught before 2026-09-01
# because PaperBroker stores the untruncated setup_id (no MT5 comment-length
# constraint applies to it) and ORB had only ever run in Paper mode -- a
# fidelity gap between Paper and MT5Broker this specific bug depended on to
# stay hidden. Confirmed empirically before fixing: see the smoke-test
# session in XAUUSD_ORB_SESSION_HANDOFF.md.
STRATEGY_TAG = "setup_xauusd_orb"


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


def _log_sizing(symbol: str, trade_manager: TradeManager, setup_id: str) -> None:
    """Records what the position sizer actually did, and warns when it had to
    exceed the risk budget.

    The volume_min clamp can only ever raise risk: when the budget buys less
    than one minimum lot the venue has no smaller size to sell, so the trade
    goes on at more than the requested percentage. Nothing surfaced that before
    2026-09-09, when an NDX100 Demo entry sized 0.01 lots against a 172.5-point
    stop and risked 0.688% of a $5,008 account instead of 0.5%. Only NDX100 is
    affected at this account size, and it stops being affected once equity is
    large enough to buy a lot inside the budget -- so this logs the condition
    rather than blocking the trade.
    """
    sizer = trade_manager._position_sizer
    s = getattr(sizer, "last_sizing", None) if sizer is not None else None
    if s is None:
        return
    _log_trade_event("sizing", symbol=symbol, setup_id=setup_id, volume=s.volume,
                     wanted_volume=round(s.wanted_volume, 4),
                     risk_amount=round(s.risk_amount, 2),
                     actual_risk=round(s.actual_risk, 2),
                     risk_multiple=round(s.risk_multiple, 3),
                     clamped_to_min=s.clamped_to_min)
    if s.clamped_to_min:
        want_pct = sizer.risk_per_trade_pct * 100
        logger.warning(
            "RISK BUDCESI ASILDI %s: %.4f lot lazim idi, minimum %.2f verildi -- "
            "risk $%.2f evezine $%.2f (%.2fx; hedef %.3f%%, faktiki %.3f%%)",
            symbol, s.wanted_volume, s.volume, s.risk_amount, s.actual_risk,
            s.risk_multiple, want_pct, want_pct * s.risk_multiple,
        )

def _evaluate_for_new_trade(
    trade_manager: TradeManager,
    broker: IBroker,
    strategy: XauusdOrbLiquiditySweepStrategy,
    bars: list[Bar],
    symbol: str,
    timeframe: Timeframe,
    grace_bars: int,
    kill_switch_flag_path: Path | None = None,
) -> None:
    """Replays every fetched bar through the strategy in chronological order
    (a fresh strategy instance only ever sees ONE bar per evaluate() call
    otherwise, so its day-scoped state would never accumulate), and acts on the
    newest setup produced within the last `grace_bars` bars -- see
    SIGNAL_GRACE_MINUTES above for why the final bar alone is not enough.
    """
    market_state = MarketState(symbol=symbol, timeframe=timeframe)
    setup = None
    bars_since_signal = 0
    for i, b in enumerate(bars):
        market_state.append_bar(b)
        found = strategy.evaluate(market_state)
        if found is not None:
            setup, bars_since_signal = found, len(bars) - 1 - i

    # A multi-day replay legitimately produces one setup per day (the strategy
    # resets its per-day latch at each date change), so the newest setup found
    # can belong to YESTERDAY when today has not broken out yet. Those are not
    # near-misses and must not be logged as such -- on 2026-09-09 the first
    # version of this guard reported XAUUSD 1349 bars and SPX500 2156 bars stale
    # on every single poll, which is noise that would bury a real one.
    if setup is not None:
        same_day = setup.timestamp.astimezone(NY).date() == bars[-1].timestamp.astimezone(NY).date()
        if not same_day:
            setup = None
        elif bars_since_signal > grace_bars:
            # Report only the near-miss window. Each poll re-derives the same
            # setup, so an unactioned signal would otherwise log an expiry every
            # two minutes until the session ends -- 748 lines on 2026-09-09,
            # JP225 alone counting from 55 bars stale up to 424. The first few
            # are the informative ones; after that it is the same fact repeated.
            if bars_since_signal <= grace_bars * 5:
                logger.info("Signal for %s is %d bars old (grace %d); too late to act on.",
                            symbol, bars_since_signal, grace_bars)
                _log_trade_event("signal_expired", symbol=symbol, setup_id=setup.setup_id,
                                 bars_since_signal=bars_since_signal)
            setup = None

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
        _log_trade_event("trade_opened", symbol=symbol, setup_id=setup.setup_id,
                         order_id=order.order_id, fill_price=order.fill_price,
                         bars_since_signal=bars_since_signal)
        _log_sizing(symbol, trade_manager, setup.setup_id)
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
    strategy: XauusdOrbLiquiditySweepStrategy,
    symbol: str,
    timeframe: Timeframe,
    timeframe_str: str,
    lookback_days: int,
    kill_switch_flag_path: Path | None = None,
) -> None:
    bars_per_day = {"M1": 1440, "M5": 288, "M15": 96, "M30": 48, "H1": 24, "H4": 6}.get(timeframe_str, 288)
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

    _evaluate_for_new_trade(trade_manager, broker, strategy, bars, symbol, timeframe,
                            _grace_bars(timeframe_str), kill_switch_flag_path)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    timeframe = Timeframe[args.timeframe]

    global _CURRENT_MODE
    _CURRENT_MODE = "paper" if args.paper else "live"

    # Deliberately separate paper state/kill-switch files (see
    # run_live_sr_bias.py's identical per-symbol rationale) -- symbol-tagged
    # so a future second instance (e.g. a different broker suffix) doesn't
    # collide.
    symbol_tag = args.symbol.lower().replace(".", "_")
    risk_dir = Path(__file__).parent / "risk"
    kill_switch_flag_path = risk_dir / f"kill_switch_xauusd_orb_{symbol_tag}_paper.flag" if args.paper else None
    daily_risk_tracker = (
        DailyRiskTracker(
            state_file=risk_dir / f"daily_risk_state_xauusd_orb_{symbol_tag}_paper.json",
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
        PaperBroker(connector=connector, timeframe=args.timeframe, state_file=risk_dir / f"paper_broker_state_xauusd_orb_{symbol_tag}.json")
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
                activate_kill_switch(f"run_live_xauusd_orb.py: {exc}")
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

        sweep_config = XauusdOrbLiquiditySweepConfig()
        if args.entry_window_end:
            hh, mm = (int(p) for p in args.entry_window_end.split(":"))
            sweep_config = replace(sweep_config, entry_window_end=dtime(hh, mm))
        strategy = XauusdOrbLiquiditySweepStrategy(config=sweep_config)
        position_sizer = PositionSizer(risk_per_trade_pct=args.risk_per_trade_pct)
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
