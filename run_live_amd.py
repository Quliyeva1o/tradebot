#!/usr/bin/env python3
"""Paper trading loop: gold AMD (strategy/gold_amd.py) on PaperBroker.

PAPER ONLY. This bot has never traded: the backtest behind it is 74 trades on CFI and 33 on
FundingPips (see the strategy module's docstring for what is and is not known), so it starts as a
measurement. It refuses to run without --paper; promoting it to a Demo bot means writing that
code path on purpose, with a stop rule from deploy/kill_rules.json, not flipping a flag.

One poll (every 2 minutes, from a Scheduled Task) does one of three things:
  - a position is open: close it at 15:55 New York (or when it is left over from an earlier day),
    otherwise let TradeManager check its stop and target against the bars closed since it opened;
  - flat inside the 10:00-15:00 New York entry window: re-derive today's setup from the last
    21 days of M1 bars and act on it if its entry fell due in the last SIGNAL_GRACE_SECONDS;
  - flat outside the window: nothing, and no bars are fetched.

The 21 days are for the 4H bias, which depends on its own history: on 704 sample days a 14-day
window gave the same bias as the full 2024-2026 history every time.

Usage:
    python run_live_amd.py --symbol XAUUSD --tp-r 2.0 --min-fvg 0.5 --spread-floor 0.15 --paper
"""

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config.brokers import UnknownBrokerError
from core.models import Bar, OrderType, SignalDirection
from execution.interfaces import IBroker
from execution.models import Position, TradeManagerAction
from execution.order import OrderStatus
from execution.paper_broker import PaperBroker
from execution.position_sizer import PositionSizer
from execution.trade_manager import TradeManager
from execution.traded_setups import already_traded, record_traded
from mt5 import clock
from mt5.connector import MT5Connector, WrongBrokerError, ensure_logged_into, resolve_ticker
from risk.daily_risk_tracker import DailyRiskTracker
from risk.kill_switch import is_trading_halted
from strategy.gold_amd import SETUP_TAG, GoldAmdConfig, GoldAmdStrategy
from strategy.risk_reward import resolve_stop_and_target
from utils.logging import setup_logger, setup_structured_logger

NY = ZoneInfo("America/New_York")

logger = setup_logger("run_live_amd", log_to_file=True)
trade_events_logger = setup_structured_logger("trade_events")

DEFAULT_LOOKBACK_DAYS = 21
DEFAULT_RISK_PER_TRADE_PCT = 0.005
# An entry is due the minute a 5m bar closes and a poll comes every two minutes, so four minutes
# covers the cadence and jitter without chasing a trade the research did not describe.
SIGNAL_GRACE_SECONDS = 240
# Bars fetched to manage an open position: it can only be from today or the evening before.
MANAGE_LOOKBACK_BARS = 2 * 1440

_CURRENT_MODE = "paper"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gold AMD paper-trading loop (see module docstring).")
    parser.add_argument("--symbol", required=True,
                        help="Symbol as THIS REPO names it (XAUUSD); the broker's ticker is resolved from .env")
    parser.add_argument("--tp-r", type=float, default=2.0, help="Take-profit R multiple")
    parser.add_argument("--min-fvg", type=float, default=0.5,
                        help="Narrowest fair-value gap, in price points, that counts as a zone")
    parser.add_argument("--spread-floor", type=float, default=0.0,
                        help="Spread (price points) never assumed below: MT5 bars store the minute's smallest")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--volume", type=float, default=0.1)
    parser.add_argument("--risk-per-trade-pct", type=float, default=DEFAULT_RISK_PER_TRADE_PCT)
    parser.add_argument("--variant", type=_variant_name, default=None,
                        help="Suffix for this bot's state files (lower-case letters and digits)")
    parser.add_argument("--paper", action="store_true",
                        help="Required: this bot trades on PaperBroker only")
    args = parser.parse_args(argv)
    if not args.paper:
        parser.error("this bot is paper-only: pass --paper")
    return args


def _variant_name(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9]+", value):
        raise argparse.ArgumentTypeError(f"--variant must be lower-case letters and digits, got {value!r}")
    return value


def _bot_tag(symbol: str, variant: str | None) -> str:
    tag = symbol.lower().replace(".", "_")
    return f"{tag}_{variant}" if variant else tag


def _log_trade_event(event: str, **fields: object) -> None:
    trade_events_logger.info({"event_type": event, "mode": _CURRENT_MODE, **fields})


def _direction_from_order_type(order_type: OrderType) -> SignalDirection:
    return SignalDirection.BUY if order_type == OrderType.BUY_MARKET else SignalDirection.SELL


def _attach_to_open_position(trade_manager: TradeManager, broker: IBroker, position: Position) -> None:
    trade_manager._broker = broker
    trade_manager._position_id = position.id
    trade_manager._direction = _direction_from_order_type(position.order_type)
    trade_manager._stop_loss = position.stop_loss
    trade_manager._take_profit = position.take_profit


def _partition_positions(positions: list[Position], symbol: str,
                         tag: str = SETUP_TAG) -> tuple[list[Position], list[Position]]:
    """This symbol's open positions split into (ours, someone else's)."""
    same_symbol = [p for p in positions if p.symbol == symbol]
    return ([p for p in same_symbol if p.comment.startswith(tag)],
            [p for p in same_symbol if not p.comment.startswith(tag)])


def in_entry_window(ny_now: datetime, config: GoldAmdConfig) -> bool:
    """Weekdays, from the end of the opening range to the entry deadline, New York time."""
    return ny_now.weekday() < 5 and config.range_end <= ny_now.time() < config.entry_deadline


def flat_due(position: Position, ny_now: datetime, config: GoldAmdConfig) -> bool:
    """True from 15:55 New York, and for a position left over from an earlier New York day.

    The bot only ever opens between 10:00 and 15:00, so anything open before 10:00 is from an
    earlier day whatever its timestamp says.
    """
    if ny_now.time() >= config.flat_at or ny_now.time() < config.range_end:
        return True
    opened = position.timestamp
    return opened.tzinfo is not None and opened.astimezone(NY).date() < ny_now.date()


def _close_flat(trade_manager: TradeManager, broker: IBroker, position: Position) -> None:
    _attach_to_open_position(trade_manager, broker, position)
    action = trade_manager.close_trade()
    if action is TradeManagerAction.CLOSE_FAILED:
        result = trade_manager.last_close_result
        reason = result.comment if result is not None else "unknown"
        logger.error("Closing %s for %s at the session end FAILED: %s; the next poll retries.",
                     position.id, position.symbol, reason)
        _log_trade_event("close_failed", symbol=position.symbol, position_id=position.id, reason=reason)
        return
    logger.info("Trade %s for %s closed at the end of the New York session.", position.id, position.symbol)
    _log_trade_event("closed_session_end", symbol=position.symbol, position_id=position.id)


def _manage_open_trade(trade_manager: TradeManager, broker: IBroker, position: Position, bars: list[Bar]) -> None:
    """Checks the position's stop and target against every bar closed since it opened."""
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
        result = trade_manager.last_close_result
        logger.error("Trade %s for %s FAILED TO CLOSE: %s.", position.id, symbol,
                     result.comment if result is not None else "unknown")
        _log_trade_event("close_failed", symbol=symbol, position_id=position.id)
    else:
        logger.info("Trade %s for %s closed: %s", position.id, symbol, action.value)
        _log_trade_event("closed", symbol=symbol, position_id=position.id, outcome=action.value)


def _clock_trustworthy(symbol: str) -> bool:
    """False when the broker's clock and ours disagree by a whole hour (mt5/clock.py).

    The session boundaries are New York wall-clock times: an hour of drift builds the opening
    range from the wrong bars, a different trade from the researched one.
    """
    verdict = clock.measure(symbol)
    if not verdict.wrong:
        return True
    logger.critical("SAAT UYGUNSUZLUGU -- yeni girise icaze verilmir: %s", verdict.detail)
    _log_trade_event("entry_blocked_clock_drift", symbol=symbol,
                     drift_seconds=round(verdict.drift or 0.0, 1), hours_off=verdict.hours_off)
    return False


def _log_sizing(symbol: str, trade_manager: TradeManager, setup_id: str) -> None:
    sizer = trade_manager._position_sizer
    s = getattr(sizer, "last_sizing", None) if sizer is not None else None
    if s is None:
        return
    _log_trade_event("sizing", symbol=symbol, setup_id=setup_id, volume=s.volume,
                     wanted_volume=round(s.wanted_volume, 4), risk_amount=round(s.risk_amount, 2),
                     actual_risk=round(s.actual_risk, 2), risk_multiple=round(s.risk_multiple, 3),
                     clamped_to_min=s.clamped_to_min)


def _open_if_signal(trade_manager: TradeManager, broker: IBroker, strategy: GoldAmdStrategy, bars: list[Bar],
                    symbol: str, now: datetime, kill_switch_flag_path: Path, traded_setups_path: Path) -> None:
    setup, reason, age = strategy.find_setup(bars, symbol, now, SIGNAL_GRACE_SECONDS)
    if setup is None:
        logger.info("RESULT: NO SIGNAL (%s%s)", reason, f", {age}s old" if age is not None else "")
        _log_trade_event("no_signal", symbol=symbol, reason=reason)
        return
    if already_traded(traded_setups_path, setup.setup_id):
        logger.info("Setup %s for %s was already traded; not opening it again.", setup.setup_id, symbol)
        _log_trade_event("setup_already_traded", symbol=symbol, setup_id=setup.setup_id)
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
        record_traded(traded_setups_path, setup.setup_id)
        sl, tp = resolve_stop_and_target(setup)
        logger.info("Trade opened for %s: order_id=%s fill_price=%.5f", symbol, order.order_id, order.fill_price)
        _log_trade_event("trade_opened", symbol=symbol, setup_id=setup.setup_id, order_id=order.order_id,
                         fill_price=order.fill_price, stop_loss=sl, take_profit=tp, seconds_since_signal=age)
        _log_sizing(symbol, trade_manager, setup.setup_id)
    else:
        result = trade_manager.last_open_result
        reason = result.comment if result is not None else "unknown"
        logger.error("Trade open REJECTED for %s: reason=%s", symbol, reason)
        _log_trade_event("trade_open_rejected", symbol=symbol, setup_id=setup.setup_id, reason=reason)


def run_once(connector: MT5Connector, broker: IBroker, trade_manager: TradeManager, strategy: GoldAmdStrategy,
             symbol: str, lookback_days: int, kill_switch_flag_path: Path, traded_setups_path: Path,
             now: datetime | None = None) -> None:
    now = now if now is not None else datetime.now(UTC)
    ny_now = now.astimezone(NY)
    config = strategy.config
    mine, foreign = _partition_positions(broker.get_open_positions(), symbol)
    if len(mine) > 1:
        logger.error("Ambiguous open positions for %s (%d owned by this strategy); skipping.", symbol, len(mine))
        _log_trade_event("ambiguous_positions", symbol=symbol, count=len(mine))
        return
    if len(mine) == 1:
        if flat_due(mine[0], ny_now, config):
            _close_flat(trade_manager, broker, mine[0])
        else:
            _manage_open_trade(trade_manager, broker, mine[0],
                               connector.fetch_recent_bars(symbol, "M1", MANAGE_LOOKBACK_BARS))
        return
    if foreign:
        logger.info("Skipping %s: %d position(s) held by another strategy.", symbol, len(foreign))
        _log_trade_event("foreign_position_blocks_entry", symbol=symbol, count=len(foreign))
        return
    if not in_entry_window(ny_now, config):
        logger.info("RESULT: NO SIGNAL (outside the New York entry window, %s)", ny_now.strftime("%a %H:%M"))
        return
    if not _clock_trustworthy(symbol):
        return
    bars = connector.fetch_recent_bars(symbol, "M1", lookback_days * 1440)
    logger.info("Fetched %d M1 bar(s) for %s: %s -> %s", len(bars), symbol, bars[0].timestamp, bars[-1].timestamp)
    _open_if_signal(trade_manager, broker, strategy, bars, symbol, now, kill_switch_flag_path, traded_setups_path)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        profile, symbol = resolve_ticker(args.symbol)
    except UnknownBrokerError as exc:
        logger.critical("BROKER NOT RESOLVED: %s", exc)
        print(f"REFUSING TO START: {exc}")
        sys.exit(1)
    logger.info("Broker %s (%s): %s -> %s", profile.name, profile.server, args.symbol, symbol)

    tag = _bot_tag(symbol, args.variant)
    risk_dir = Path(__file__).parent / "risk"
    kill_switch_flag_path = risk_dir / f"kill_switch_amd_{tag}_paper.flag"
    daily_risk_tracker = DailyRiskTracker(state_file=risk_dir / f"daily_risk_state_amd_{tag}_paper.json",
                                          kill_switch_flag_path=kill_switch_flag_path)
    if is_trading_halted(kill_switch_flag_path):
        logger.info("RESULT: TRADING HALTED (kill-switch active)")
        print("TRADING HALTED (kill-switch active)")
        return

    connector = MT5Connector()
    broker = PaperBroker(connector=connector, timeframe="M1", state_file=risk_dir / f"paper_broker_state_amd_{tag}.json")
    if not broker.connect():
        logger.error("Could not connect to MT5.")
        sys.exit(1)
    try:
        try:
            ensure_logged_into(profile)
        except WrongBrokerError as exc:
            logger.critical("WRONG BROKER: %s", exc)
            print(f"REFUSING TO TRADE: {exc}")
            sys.exit(1)
        account_info = broker.get_account_info()
        logger.info("PAPER mode: balance=%.2f, equity=%.2f (no real orders will be placed).",
                    account_info.balance, account_info.equity)
        daily_risk_tracker.check_and_update(account_info.equity, account_info.login)
        strategy = GoldAmdStrategy(GoldAmdConfig(tp_r=args.tp_r, min_fvg=args.min_fvg, spread_floor=args.spread_floor))
        trade_manager = TradeManager(volume=args.volume, position_sizer=PositionSizer(risk_per_trade_pct=args.risk_per_trade_pct))
        run_once(connector, broker, trade_manager, strategy, symbol, args.lookback_days, kill_switch_flag_path,
                 risk_dir / f"traded_setups_amd_{tag}_paper.json")
    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()
