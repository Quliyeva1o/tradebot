#!/usr/bin/env python3
"""Paper bot for the 10:00 First FVG rule (strategy/first_fvg_window.py), as a working limit order.

Each poll (every 2 minutes, like every other bot):
  1. a position of ours is open -> the broker holds its stop and target, nothing to do. If
     today's setup confirmed meanwhile it is given up, because the backtest skips a setup whose
     candle 3 closes while the previous trade is open (scripts/first_fvg_window_backtest.py);
  2. another bot's position on the symbol -> wait;
  3. today's setup exists (candle 3 has closed) -> one limit order at the gap's near edge, working
     from candle 3's close until New York midnight, stop and target attached.
     execution/traded_setups.py makes sure a setup is ordered once.

The paper broker runs with level_fills=True: the limit fills when an M1 bar reaches it and the
stop or target fill at their price, the way a broker holds them. Each paper trade can therefore
be matched with its backtest twin (scripts/first_fvg_paper_parity.py).

PAPER ONLY. MT5Broker refuses order windows until they are mapped to MT5's expiration, and the
Demo account is a prop trial whose daily loss limit the ORB bots already use.

Usage:
    python run_live_first_fvg_window.py --symbol NDX100 --paper
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.append(str(Path(__file__).parent.resolve()))

from config.brokers import UnknownBrokerError
from core.models import OrderType, SignalDirection
from execution.interfaces import IBroker
from execution.models import OrderRequest
from execution.paper_broker import PaperBroker
from execution.position_sizer import PositionSizer
from execution.traded_setups import already_traded, record_traded
from mt5.connector import MT5Connector, WrongBrokerError, ensure_logged_into, resolve_ticker
from risk.daily_risk_tracker import DailyRiskTracker
from risk.kill_switch import is_trading_halted
from strategy.first_fvg_window import (
    STRATEGY_TAG,
    FirstFvgWindowConfig,
    FvgPlan,
    find_plan,
    order_expires_at,
    setup_id,
)
from utils.logging import setup_logger, setup_structured_logger

NY = ZoneInfo("America/New_York")

logger = setup_logger("run_live_first_fvg_window", log_to_file=True)
trade_events_logger = setup_structured_logger("trade_events")

SIGNAL_TIMEFRAME = "M15"
SIGNAL_BARS = 2 * 96  # two days of M15 cover today's session from any poll time
# The current FundingPips trial size, so lot sizes and the minimum-lot clamp look like the real account's.
PAPER_BALANCE = 50_000.0


def _log_trade_event(event: str, **fields: object) -> None:
    trade_events_logger.info({"event_type": event, "mode": "paper", **fields})


def _todays_plan(connector: MT5Connector, symbol: str, cfg: FirstFvgWindowConfig, now: datetime) -> FvgPlan | None:
    bars = connector.fetch_recent_bars(symbol, SIGNAL_TIMEFRAME, SIGNAL_BARS)
    today = now.astimezone(NY).date()
    return find_plan([b for b in bars if b.timestamp.astimezone(NY).date() == today], cfg)


def run_once(
    *,
    connector: MT5Connector,
    broker: IBroker,
    sizer: PositionSizer,
    symbol: str,
    cfg: FirstFvgWindowConfig,
    now: datetime,
    traded_setups_path: Path,
    kill_switch_flag_path: Path | None = None,
) -> str:
    """One poll. Returns what it did, for the log."""
    same_symbol = [p for p in broker.get_open_positions() if p.symbol == symbol]
    mine = [p for p in same_symbol if p.comment.startswith(STRATEGY_TAG)]
    plan = _todays_plan(connector, symbol, cfg, now)

    if mine:
        if plan is not None and not already_traded(traded_setups_path, setup_id(symbol, plan)):
            record_traded(traded_setups_path, setup_id(symbol, plan))
            logger.info("Setup %s confirmed while %s was open; giving it up.", setup_id(symbol, plan), mine[0].id)
            _log_trade_event("setup_skipped_position_open", symbol=symbol, setup_id=setup_id(symbol, plan))
        _log_trade_event("held", symbol=symbol, position_id=mine[0].id)
        return "held"
    if same_symbol:
        _log_trade_event("foreign_position_blocks_entry", symbol=symbol, count=len(same_symbol))
        return "foreign_position"
    if plan is None:
        _log_trade_event("no_signal", symbol=symbol)
        return "no_setup"

    sid = setup_id(symbol, plan)
    if already_traded(traded_setups_path, sid):
        return "already_traded"
    if is_trading_halted(kill_switch_flag_path):
        logger.warning("Setup %s found but the kill-switch is active; not ordering.", sid)
        _log_trade_event("signal_blocked_kill_switch", symbol=symbol, setup_id=sid)
        return "kill_switch"

    volume = sizer.calculate_size(broker.get_account_info().balance, plan.entry, plan.stop,
                                  broker.get_symbol_constraints(symbol))
    if volume <= 0:
        logger.error("Setup %s sized to %r lots; not ordering.", sid, volume)
        return "zero_volume"

    long = plan.direction == SignalDirection.BUY
    request = OrderRequest(
        symbol=symbol, order_type=OrderType.BUY_LIMIT if long else OrderType.SELL_LIMIT, volume=volume,
        price=plan.entry, stop_loss=plan.stop, take_profit=plan.target, comment=sid,
        valid_from=plan.confirm_close, expires_at=order_expires_at(plan),
    )
    result = broker.place_order(request)
    if not result.success:
        logger.error("Limit for %s rejected: %s (retcode=%s)", sid, result.comment, result.retcode)
        _log_trade_event("order_rejected", symbol=symbol, setup_id=sid, reason=result.comment, retcode=result.retcode)
        return "order_rejected"

    record_traded(traded_setups_path, sid)
    logger.info("Placed %s limit %s @ %.2f (sl %.2f, tp %.2f, %.2f lots), working until %s.",
                plan.direction.name, sid, plan.entry, plan.stop, plan.target, volume, request.expires_at)
    _log_trade_event(
        "order_placed", symbol=symbol, setup_id=sid, order_id=result.order_id,
        direction=plan.direction.name, limit=plan.entry, stop_loss=plan.stop, take_profit=plan.target,
        volume=volume, valid_from=plan.confirm_close.isoformat(), expires_at=request.expires_at.isoformat(),
        seconds_after_confirm=round((now - plan.confirm_close).total_seconds()),
    )
    return "order_placed"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="10:00 First FVG paper bot (see module docstring).")
    parser.add_argument("--symbol", required=True,
                        help="Symbol as THIS REPO names it (e.g. NDX100) -- the broker's own ticker "
                             "is resolved from .env, see config/brokers.py")
    parser.add_argument("--tp-r", type=float, default=3.0)
    parser.add_argument("--session-start", default="10:00", help="NY time of the bar that must exist")
    parser.add_argument("--c1-bars-before", type=int, default=1)
    parser.add_argument("--third-candle-before", default="11:00", help="NY time candle 3 must start before")
    parser.add_argument("--risk-per-trade-pct", type=float, default=0.005)
    parser.add_argument("--paper", action="store_true", required=True,
                        help="Required: this bot has no real-order mode yet (see module docstring).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    cfg = FirstFvgWindowConfig(
        session_start=time.fromisoformat(args.session_start), c1_bars_before=args.c1_bars_before,
        third_candle_before=time.fromisoformat(args.third_candle_before), tp_r=args.tp_r,
    )

    # This machine's broker and its own name for the symbol, resolved before the state files
    # are named from it (see run_live_nasdaq_orb.main for why that order matters).
    try:
        profile, symbol = resolve_ticker(args.symbol)
    except UnknownBrokerError as exc:
        logger.critical("BROKER NOT RESOLVED: %s", exc)
        print(f"REFUSING TO START: {exc}")
        sys.exit(1)
    logger.info("Broker %s (%s): %s -> %s", profile.name, profile.server, args.symbol, symbol)

    symbol_tag = symbol.lower().replace(".", "_")
    risk_dir = Path(__file__).parent / "risk"
    kill_switch_flag_path = risk_dir / f"kill_switch_fvg_window_{symbol_tag}_paper.flag"
    if is_trading_halted(kill_switch_flag_path):
        logger.info("RESULT: TRADING HALTED (kill-switch active)")
        return

    connector = MT5Connector()
    broker = PaperBroker(
        connector=connector, initial_balance=PAPER_BALANCE, timeframe="M1", level_fills=True,
        state_file=risk_dir / f"paper_broker_state_fvg_window_{symbol_tag}.json",
    )
    if not broker.connect():
        logger.error("Could not connect to MT5.")
        sys.exit(1)
    try:
        try:
            ensure_logged_into(profile)
        except WrongBrokerError as exc:
            logger.critical("WRONG BROKER: %s", exc)
            print(f"REFUSING TO RUN: {exc}")
            sys.exit(1)
        account_info = broker.get_account_info()
        DailyRiskTracker(
            state_file=risk_dir / f"daily_risk_state_fvg_window_{symbol_tag}_paper.json",
            kill_switch_flag_path=kill_switch_flag_path,
        ).check_and_update(account_info.equity, account_info.login)
        outcome = run_once(
            connector=connector, broker=broker, sizer=PositionSizer(risk_per_trade_pct=args.risk_per_trade_pct),
            symbol=symbol, cfg=cfg, now=datetime.now(UTC),
            traded_setups_path=risk_dir / f"traded_setups_fvg_window_{symbol_tag}_paper.json",
            kill_switch_flag_path=kill_switch_flag_path,
        )
        logger.info("RESULT: %s (balance %.2f, equity %.2f)", outcome, account_info.balance, account_info.equity)
    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()
