#!/usr/bin/env python3
"""Live trading loop: SR Daily Bias strategy (pine scriptlerim/
SR_Daily_Bias_Strategy.pine, liquidity-TP variant) wired into TradeManager/
MT5Broker -- structural clone of run_live_midnight_fvg.py's two-layer
demo-account safety rail, kill-switch gate, and paper-mode risk-state
isolation, with SrDailyBiasStrategy swapped in.

SAFETY -- THIS SCRIPT PLACES REAL (DEMO-ACCOUNT) ORDERS UNLESS --paper IS
PASSED. See run_live_demo.py's module docstring for the full two-layer
demo-account enforcement description; reused unchanged here.

IMPORTANT differences from run_live_midnight_fvg.py:
- No session-window gate and no one-trade-per-day cap -- this strategy
  evaluates every closed M15 (or configured TF) bar all day, and the ONLY
  thing preventing overlapping entries is the one-position-at-a-time check
  in run_once() (identical in spirit to the Midnight FVG script's own
  check), same as every other strategy in this codebase.
- Needs a SECOND data feed (D1 bars) for the Daily Bias cross-timeframe
  input -- see strategy/sr_daily_bias.py's DailyBiasContext /
  compute_daily_bias_context(). Fetched once per invocation alongside the
  primary-timeframe bars.
- No "daily resolved" skip-cache (unlike Midnight FVG): since this strategy
  can fire multiple times per day and isn't session-scoped, there's no
  well-defined "nothing more can happen today" state to cache.
- No fast-poll extension (yet): entries are bar-close-triggered (touch/
  breakout/retest all evaluated on CLOSED M15 bars, matching the validated
  backtest), so running this every few minutes already catches each new
  bar shortly after it closes without needing sub-bar polling.

RISK SIZING: the session's own drawdown/streak analysis (see project notes)
found this strategy carries roughly 2x the drawdown-per-unit-risk of the
Midnight FVG strategy at the same nominal risk %. Calibrated default here is
therefore LOWER than Midnight FVG's own default -- see --risk-per-trade-pct.

Usage:
    python run_live_sr_bias.py --symbol XAUUSD --timeframe M15 --paper
"""

import argparse
import sys
from datetime import UTC, datetime
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
from strategy.sr_daily_bias import (
    SrDailyBiasConfig,
    SrDailyBiasStrategy,
    compute_daily_bias_context,
)
from utils.logging import setup_logger, setup_structured_logger
from market_structure.structure_models import MarketState

logger = setup_logger("run_live_sr_bias", log_to_file=True)
trade_events_logger = setup_structured_logger("trade_events")

# M15 default: ~120 trading days gives ample warmup for ATR/ADX/vol-SMA (all
# <=20-period) and a deep-enough swing/liquidity pool, while keeping the
# per-invocation replay small (~120*96 M15 bars/day upper bound =~ 11.5k).
DEFAULT_LOOKBACK_DAYS = 120
DEFAULT_DAILY_LOOKBACK_DAYS = 200  # D1 bars for the bias EMA(20) + its own warmup
DEFAULT_VOLUME = 0.1
DEFAULT_RISK_PER_TRADE_PCT = 0.002  # 0.20% -- calibrated to ~10% max drawdown, see module docstring

_CURRENT_MODE = "live"


class DemoAccountRequiredError(RuntimeError):
    """Raised when this script is not explicitly and verifiably pointed at a demo account."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Live SR Daily Bias loop against a DEMO MT5 account (places real demo orders -- see module docstring)."
    )
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--daily-lookback-days", type=int, default=DEFAULT_DAILY_LOOKBACK_DAYS)
    parser.add_argument("--volume", type=float, default=DEFAULT_VOLUME)
    parser.add_argument("--risk-per-trade-pct", type=float, default=DEFAULT_RISK_PER_TRADE_PCT)
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Use PaperBroker (virtual fills against real MT5 prices, no real orders) instead of "
        "MT5Broker. RECOMMENDED for an extended trial before ever running without this flag -- "
        "this strategy has NOT yet run in any live/paper capacity, unlike Midnight FVG.",
    )
    return parser.parse_args(argv)


def _ensure_explicit_demo_configuration() -> None:
    """Identical gate to run_live_demo.py -- see that module for the full rationale."""
    account_type = Settings.load().MT5_ACCOUNT_TYPE.strip().lower()
    if account_type != "demo":
        raise DemoAccountRequiredError(
            f"MT5_ACCOUNT_TYPE must be explicitly set to 'demo' in .env to run "
            f"run_live_sr_bias.py (got {account_type!r}). Refusing to start."
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


def _manage_open_trade(trade_manager: TradeManager, broker: IBroker, position: Position, bars: list[Bar]) -> None:
    """Checks the open position's SL/TP against every bar closed SINCE it
    opened, not just the newest one -- see run_live_midnight_fvg.py's
    identical _manage_open_trade() docstring for the full rationale (the
    2-minute poll interval can let more than one M15/M30 bar close between
    invocations, and PaperBroker relies entirely on this bar-by-bar check
    to simulate fills; live/demo mode's real MT5 SL/TP order is unaffected
    either way).
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
    strategy: SrDailyBiasStrategy,
    bars: list[Bar],
    daily_bars: list[Bar],
    symbol: str,
    timeframe: Timeframe,
    kill_switch_flag_path: Path | None = None,
) -> None:
    """Replays every fetched bar through the strategy in chronological order
    (same one-shot-replay pattern as run_live_midnight_fvg.py -- a fresh
    strategy instance only ever sees ONE bar per evaluate() call otherwise,
    so state built up over the lookback would never accumulate), pushing a
    fresh DailyBiasContext each time the calendar date changes, and acts
    only on the setup -- if any -- returned for the FINAL (newest) bar.
    """
    market_state = MarketState(symbol=symbol, timeframe=timeframe)
    setup = None
    last_context_date = None
    for b in bars:
        d = b.timestamp.date()
        if d != last_context_date:
            ctx = compute_daily_bias_context(
                daily_bars, for_date=d,
                ema_len=strategy.config.daily_bias_len,
                neutral_pct=strategy.config.bias_neutral_pct,
            )
            if ctx is not None:
                strategy.set_daily_bias_context(ctx)
            last_context_date = d
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
    strategy: SrDailyBiasStrategy,
    symbol: str,
    timeframe: Timeframe,
    timeframe_str: str,
    lookback_days: int,
    daily_lookback_days: int,
    kill_switch_flag_path: Path | None = None,
) -> None:
    bars_per_day = {"M1": 1440, "M5": 288, "M15": 96, "M30": 48, "H1": 24, "H4": 6}.get(timeframe_str, 96)
    lookback_bars = lookback_days * bars_per_day
    bars = connector.fetch_recent_bars(symbol, timeframe_str, lookback_bars)
    daily_bars = connector.fetch_recent_bars(symbol, "D1", daily_lookback_days)
    logger.info(
        "Fetched %d %s bar(s) for %s: %s -> %s (+ %d D1 bars for bias)",
        len(bars), timeframe_str, symbol, bars[0].timestamp, bars[-1].timestamp, len(daily_bars),
    )

    open_positions = [p for p in broker.get_open_positions() if p.symbol == symbol]
    if len(open_positions) > 1:
        logger.error("Ambiguous open positions for %s (%d found); skipping.", symbol, len(open_positions))
        _log_trade_event("ambiguous_positions", symbol=symbol, count=len(open_positions))
        return
    if len(open_positions) == 1:
        _manage_open_trade(trade_manager, broker, open_positions[0], bars)
        return

    _evaluate_for_new_trade(trade_manager, broker, strategy, bars, daily_bars, symbol, timeframe, kill_switch_flag_path)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    timeframe = Timeframe[args.timeframe]

    global _CURRENT_MODE
    _CURRENT_MODE = "paper" if args.paper else "live"

    # Deliberately separate paper state/kill-switch files per symbol (and from
    # every other bot's) -- see run_live_midnight_fvg.py's identical rationale
    # (isolating paper-mode virtual baselines across bots that might run in
    # parallel against the same demo account). Without the symbol suffix,
    # running this script for two symbols at once (e.g. XAUUSD 15m + NAS100
    # 30m) would share one kill-switch flag and one daily-loss tracker,
    # letting one symbol's daily loss limit halt the other's trading.
    symbol_tag = args.symbol.lower()
    risk_dir = Path(__file__).parent / "risk"
    kill_switch_flag_path = risk_dir / f"kill_switch_sr_bias_{symbol_tag}_paper.flag" if args.paper else None
    daily_risk_tracker = (
        DailyRiskTracker(
            state_file=risk_dir / f"daily_risk_state_sr_bias_{symbol_tag}_paper.json",
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
                activate_kill_switch(f"run_live_sr_bias.py: {exc}")
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

        strategy = SrDailyBiasStrategy(config=SrDailyBiasConfig())
        position_sizer = PositionSizer(risk_per_trade_pct=args.risk_per_trade_pct)
        trade_manager = TradeManager(volume=args.volume, position_sizer=position_sizer)
        run_once(
            connector=connector, broker=broker, trade_manager=trade_manager, strategy=strategy,
            symbol=args.symbol, timeframe=timeframe, timeframe_str=args.timeframe,
            lookback_days=args.lookback_days, daily_lookback_days=args.daily_lookback_days,
            kill_switch_flag_path=kill_switch_flag_path,
        )
    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()
