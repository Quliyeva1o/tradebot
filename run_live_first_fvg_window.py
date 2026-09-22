#!/usr/bin/env python3
"""Bot for the 10:00 First FVG rule (strategy/first_fvg_window.py), as a working limit order.

Each poll (every 2 minutes, like every other bot):
  1. a position of ours is open -> the broker holds its stop and target, nothing to do. If
     today's setup confirmed meanwhile it is given up, because the backtest skips a setup whose
     candle 3 closes while the previous trade is open (scripts/first_fvg_window_backtest.py);
  2. another bot's position on the symbol -> wait;
  3. today's setup exists (candle 3 has closed) -> one limit order at the gap's near edge, working
     from candle 3's close until New York midnight, stop and target attached.
     execution/traded_setups.py makes sure a setup is ordered once.

--paper runs PaperBroker with level_fills=True: the limit fills when an M1 bar reaches it and the
stop or target fill at their price, the way a broker holds them. Each paper trade can therefore
be matched with its backtest twin (scripts/first_fvg_paper_parity.py).

Without --paper it places REAL orders on a DEMO account, behind the same two safety rails as
run_live_nasdaq_orb.py (MT5_ACCOUNT_TYPE=demo in .env, and a terminal reporting a demo account).
Three things a real broker needs that paper does not:
  * the limit's expiry goes to MT5 on the broker's clock (execution/mt5_broker._mt5_expiration),
    and each poll also cancels an order of ours whose expiry has passed -- so a wrong clock can
    end a limit early but never leave one working past the session;
  * MT5 refuses a buy limit at or above the ask. When price has already come back through the
    entry by the time the order goes in, the backtest fills at that bar's open, the better price
    (execution/level_fill.limit_fill) -- so the bot enters at market instead, unless price is
    already through the stop too, when the backtest's trade has lost and there is none to take;
  * a halted bot cancels the limit it left working, which would otherwise still open a trade.

Usage:
    python run_live_first_fvg_window.py --symbol NDX100 --paper
    python run_live_first_fvg_window.py --symbol NDX100 --risk-per-trade-pct 0.0025
"""

from __future__ import annotations

import argparse
import shlex
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.append(str(Path(__file__).parent.resolve()))

import MetaTrader5 as mt5  # noqa: N813

from config.brokers import UnknownBrokerError
from config.settings import Settings
from core.models import AccountInfo, OrderType, SignalDirection
from execution.interfaces import IBroker
from execution.models import OrderRequest
from execution.mt5_broker import MT5Broker
from execution.paper_broker import PaperBroker
from execution.position_sizer import PositionSizer
from execution.traded_setups import already_traded, record_traded
from mt5 import clock
from mt5.connector import MT5Connector, WrongBrokerError, ensure_logged_into, resolve_ticker
from risk.daily_risk_tracker import DailyRiskTracker
from risk.kill_switch import activate_kill_switch, is_trading_halted
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

_CURRENT_MODE = "paper"


class DemoAccountRequiredError(RuntimeError):
    """Raised when this script is not explicitly and verifiably pointed at a demo account."""


@dataclass(frozen=True)
class Quote:
    """The live price the entry is checked against, and how close to it MT5 lets an order rest."""

    bid: float
    ask: float
    min_gap: float  # SYMBOL_TRADE_STOPS_LEVEL in price: a limit or stop closer than this is refused


def _log_trade_event(event: str, **fields: object) -> None:
    trade_events_logger.info({"event_type": event, "mode": _CURRENT_MODE, **fields})


def _todays_plan(connector: MT5Connector, symbol: str, cfg: FirstFvgWindowConfig, now: datetime) -> FvgPlan | None:
    bars = connector.fetch_recent_bars(symbol, SIGNAL_TIMEFRAME, SIGNAL_BARS)
    today = now.astimezone(NY).date()
    return find_plan([b for b in bars if b.timestamp.astimezone(NY).date() == today], cfg)


def _to_tick(price: float, tick_size: float) -> float:
    """A real broker takes prices on its tick grid; entry + 3R can land between two ticks."""
    return round(round(price / tick_size) * tick_size, 10)


def cancel_our_orders(broker: IBroker, symbol: str, now: datetime, *, all_of_them: bool) -> int:
    """Cancels this bot's resting orders on `symbol`: every one, or those past their expiry.

    The broker is sent the expiry too, so this is the second line: MT5 reads that expiry on its own
    clock, and if the conversion were ever an hour late the limit would work into the next session.
    An order of ours with no expiry at all cannot have come from this bot working correctly, so it
    is cancelled as well rather than left to work until someone notices.
    """
    cancelled = 0
    for order in broker.get_pending_orders(symbol):
        if not order.comment.startswith(STRATEGY_TAG):
            continue
        due = all_of_them or order.expires_at is None or now >= order.expires_at
        if not due:
            continue
        ok = broker.cancel_order(order.id)
        cancelled += ok
        reason = "halted" if all_of_them else ("no_expiry" if order.expires_at is None else "expired")
        (logger.info if ok else logger.error)("Cancel %s order %s (%s): %s.", symbol, order.id, reason,
                                              "done" if ok else "FAILED, next poll retries")
        _log_trade_event("order_cancelled" if ok else "order_cancel_failed", symbol=symbol,
                         order_id=order.id, reason=reason,
                         expires_at=order.expires_at.isoformat() if order.expires_at else None)
    return cancelled


def _entry_request(plan: FvgPlan, sid: str, symbol: str, volume: float, tick_size: float | None,
                   quote: Quote | None) -> OrderRequest | str:
    """The order that enters `plan`, or why none should go in.

    Paper (quote None) always rests the limit: PaperBroker fills one that price has already passed
    at the next bar's open, as the backtest does. A real broker refuses such a limit, so when the
    quote has already reached the entry the same better-priced fill is taken at market.
    """
    entry, stop, target = plan.entry, plan.stop, plan.target
    if tick_size:
        entry, stop, target = (_to_tick(p, tick_size) for p in (entry, stop, target))
    long = plan.direction == SignalDirection.BUY
    window = {"valid_from": plan.confirm_close, "expires_at": order_expires_at(plan)}
    limit = OrderRequest(symbol=symbol, order_type=OrderType.BUY_LIMIT if long else OrderType.SELL_LIMIT,
                         volume=volume, price=entry, stop_loss=stop, take_profit=target, comment=sid, **window)
    if quote is None:
        return limit

    reached = (quote.ask - entry < quote.min_gap) if long else (entry - quote.bid < quote.min_gap)
    if not reached:
        return limit
    # The exit side of the quote must still clear the stop by the broker's minimum distance.
    through_stop = (quote.bid - stop < quote.min_gap) if long else (stop - quote.ask < quote.min_gap)
    if through_stop:
        return "price_beyond_stop"
    return OrderRequest(symbol=symbol, order_type=OrderType.BUY_MARKET if long else OrderType.SELL_MARKET,
                        volume=volume, stop_loss=stop, take_profit=target, comment=sid)


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
    quote: Callable[[str], Quote] | None = None,
) -> str:
    """One poll. Returns what it did, for the log.

    `quote` is given only for a real broker (see _entry_request); with it, each poll first
    cancels any order of ours whose expiry has passed.
    """
    if quote is not None:
        cancel_our_orders(broker, symbol, now, all_of_them=False)
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
    # A session boundary an hour out builds this plan from the wrong candles -- see
    # mt5/clock.py. Entry only: an open position is left to its broker-side SL/TP.
    verdict = clock.measure(symbol)
    if verdict.wrong:
        logger.critical("SAAT UYGUNSUZLUGU -- yeni girise icaze verilmir: %s", verdict.detail)
        _log_trade_event("entry_blocked_clock_drift", symbol=symbol, setup_id=sid,
                         drift_seconds=round(verdict.drift or 0.0, 1), hours_off=verdict.hours_off)
        return "clock_drift"
    if is_trading_halted(kill_switch_flag_path):
        logger.warning("Setup %s found but the kill-switch is active; not ordering.", sid)
        _log_trade_event("signal_blocked_kill_switch", symbol=symbol, setup_id=sid)
        return "kill_switch"

    constraints = broker.get_symbol_constraints(symbol)
    volume = sizer.calculate_size(broker.get_account_info().balance, plan.entry, plan.stop, constraints)
    if volume <= 0:
        logger.error("Setup %s sized to %r lots; not ordering.", sid, volume)
        return "zero_volume"

    live_quote = quote(symbol) if quote is not None else None
    request = _entry_request(plan, sid, symbol, volume, constraints.tick_size if quote is not None else None,
                             live_quote)
    if isinstance(request, str):
        # Price ran through the entry AND the stop between candle 3's close and this poll: the
        # backtest's trade is already a loss, and there is no trade left to enter.
        record_traded(traded_setups_path, sid)
        logger.warning("Setup %s: price already beyond the stop (bid %.2f ask %.2f); not entering.",
                       sid, live_quote.bid if live_quote else 0.0, live_quote.ask if live_quote else 0.0)
        _log_trade_event("entry_skipped_beyond_stop", symbol=symbol, setup_id=sid, stop_loss=plan.stop,
                         bid=live_quote.bid if live_quote else None, ask=live_quote.ask if live_quote else None)
        return request

    result = broker.place_order(request)
    at_market = request.order_type in (OrderType.BUY_MARKET, OrderType.SELL_MARKET)
    if not result.success:
        logger.error("%s for %s rejected: %s (retcode=%s)", "Market entry" if at_market else "Limit",
                     sid, result.comment, result.retcode)
        _log_trade_event("order_rejected", symbol=symbol, setup_id=sid, reason=result.comment, retcode=result.retcode)
        return "order_rejected"

    record_traded(traded_setups_path, sid)
    sizing = getattr(sizer, "last_sizing", None)
    common = dict(symbol=symbol, setup_id=sid, order_id=result.order_id, direction=plan.direction.name,
                  limit=request.price if request.price is not None else plan.entry,
                  stop_loss=request.stop_loss, take_profit=request.take_profit, volume=volume,
                  valid_from=plan.confirm_close.isoformat(), expires_at=order_expires_at(plan).isoformat(),
                  seconds_after_confirm=round((now - plan.confirm_close).total_seconds()),
                  clamped_to_min=getattr(sizing, "clamped_to_min", None))
    if at_market:
        logger.info("Price had already reached %s's limit %.2f; entered at market: order %s fill %.2f "
                    "(sl %.2f, tp %.2f, %.2f lots).", sid, plan.entry, result.order_id, result.price,
                    request.stop_loss, request.take_profit, volume)
        _log_trade_event("entered_at_market", fill_price=result.price,
                         bid=live_quote.bid if live_quote else None, ask=live_quote.ask if live_quote else None,
                         **common)
        return "entered_at_market"
    logger.info("Placed %s limit %s @ %.2f (sl %.2f, tp %.2f, %.2f lots), working until %s.",
                plan.direction.name, sid, request.price, request.stop_loss, request.take_profit, volume,
                request.expires_at)
    _log_trade_event("order_placed", **common)
    return "order_placed"


def _mt5_quote(symbol: str) -> Quote:
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None:
        raise RuntimeError(f"No quote for {symbol}: {mt5.last_error()}")
    return Quote(bid=float(tick.bid), ask=float(tick.ask),
                 min_gap=float(info.trade_stops_level) * float(info.point))


def _ensure_explicit_demo_configuration() -> None:
    """Identical gate to run_live_demo.py -- see that module for the full rationale."""
    account_type = Settings.load().MT5_ACCOUNT_TYPE.strip().lower()
    if account_type != "demo":
        raise DemoAccountRequiredError(
            f"MT5_ACCOUNT_TYPE must be explicitly set to 'demo' in .env to run "
            f"run_live_first_fvg_window.py without --paper (got {account_type!r}). Refusing to start."
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="10:00 First FVG bot (see module docstring).")
    parser.add_argument("--symbol", required=True,
                        help="Symbol as THIS REPO names it (e.g. NDX100) -- the broker's own ticker "
                             "is resolved from .env, see config/brokers.py")
    parser.add_argument("--tp-r", type=float, default=3.0)
    parser.add_argument("--session-start", default="10:00", help="NY time of the bar that must exist")
    parser.add_argument("--c1-bars-before", type=int, default=1)
    parser.add_argument("--third-candle-before", default="11:00", help="NY time candle 3 must start before")
    parser.add_argument("--risk-per-trade-pct", type=float, default=0.005)
    parser.add_argument("--paper", action="store_true",
                        help="Virtual fills against real MT5 prices. Without it the bot places REAL orders "
                             "on a DEMO account, behind the safety rails in the module docstring.")
    return parser.parse_args(argv)


def launcher_args(bat: Path) -> argparse.Namespace:
    """The arguments a run_live_fvg_*.bat launcher starts this bot with, read by this bot's parser.

    For the reports and the stop-rule envelope, so they describe the bot that is really deployed.
    """
    line = next(line for line in bat.read_text(encoding="utf-8").splitlines() if Path(__file__).name in line)
    argv = shlex.split(line, posix=False)
    return parse_args(argv[argv.index(Path(__file__).name) + 1:])


def config_from(args: argparse.Namespace) -> FirstFvgWindowConfig:
    return FirstFvgWindowConfig(
        session_start=time.fromisoformat(args.session_start), c1_bars_before=args.c1_bars_before,
        third_candle_before=time.fromisoformat(args.third_candle_before), tp_r=args.tp_r,
    )


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    global _CURRENT_MODE
    _CURRENT_MODE = "paper" if args.paper else "live"
    cfg = config_from(args)

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
    if args.paper:
        kill_switch_flag_path: Path | None = risk_dir / f"kill_switch_fvg_window_{symbol_tag}_paper.flag"
        daily_risk_tracker = DailyRiskTracker(
            state_file=risk_dir / f"daily_risk_state_fvg_window_{symbol_tag}_paper.json",
            kill_switch_flag_path=kill_switch_flag_path,
        )
        traded_setups_path = risk_dir / f"traded_setups_fvg_window_{symbol_tag}_paper.json"
    else:
        try:
            _ensure_explicit_demo_configuration()
        except DemoAccountRequiredError as exc:
            logger.critical("DEMO-ACCOUNT SAFETY RAIL TRIPPED (config): %s", exc)
            print(f"REFUSING TO START: {exc}")
            sys.exit(1)
        # The real account's own daily-loss baseline and halt, shared with every Demo bot on it --
        # the same defaults run_live_nasdaq_orb.py uses, because the loss limit is the account's.
        kill_switch_flag_path = None
        daily_risk_tracker = DailyRiskTracker()
        traded_setups_path = risk_dir / f"traded_setups_fvg_window_{symbol_tag}.json"

    halted = is_trading_halted(kill_switch_flag_path)
    if halted and args.paper:
        logger.info("RESULT: TRADING HALTED (kill-switch active)")
        return

    connector = MT5Connector()
    broker: IBroker = (
        PaperBroker(connector=connector, initial_balance=PAPER_BALANCE, timeframe="M1", level_fills=True,
                    state_file=risk_dir / f"paper_broker_state_fvg_window_{symbol_tag}.json")
        if args.paper else MT5Broker(connector=connector)
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
        if not args.paper:
            try:
                _ensure_demo_trade_mode(account_info)
            except DemoAccountRequiredError as exc:
                logger.critical("DEMO-ACCOUNT SAFETY RAIL TRIPPED (MT5 account): %s", exc)
                activate_kill_switch(f"run_live_first_fvg_window.py: {exc}")
                print(f"REFUSING TO TRADE: {exc}")
                sys.exit(1)
            if halted:
                # Nothing else runs while halted, but a limit left working would still open a trade.
                cancel_our_orders(broker, symbol, datetime.now(UTC), all_of_them=True)
                logger.info("RESULT: TRADING HALTED (kill-switch active)")
                return
            logger.info("Demo-account safety rail passed: trade_mode=%s, currency=%s, equity=%.2f.",
                        account_info.trade_mode, account_info.currency, account_info.equity)
        daily_risk_tracker.check_and_update(account_info.equity, account_info.login)
        outcome = run_once(
            connector=connector, broker=broker, sizer=PositionSizer(risk_per_trade_pct=args.risk_per_trade_pct),
            symbol=symbol, cfg=cfg, now=datetime.now(UTC), traded_setups_path=traded_setups_path,
            kill_switch_flag_path=kill_switch_flag_path, quote=None if args.paper else _mt5_quote,
        )
        logger.info("RESULT: %s (balance %.2f, equity %.2f)", outcome, account_info.balance, account_info.equity)
    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()
