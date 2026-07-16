#!/usr/bin/env python3
"""Read-only, one-shot live signal check for NasdaqMidlineSweepStrategy.

SAFETY -- THIS SCRIPT IS READ-ONLY. It never calls mt5.order_send,
mt5.order_check, mt5.order_calc_margin, or any other MT5 trading/position-
modifying API. It only calls MT5Connector.fetch_recent_bars() (itself
documented read-only -- see mt5/connector.py) and evaluates a strategy
against the fetched bars in memory. Safe to run against a live (non-demo)
account for signal inspection.

THIS IS NOT A LIVE-TRADING DAEMON. It runs ONCE: connect, fetch, evaluate,
print, exit. It answers exactly one question -- "as of the most recently
closed bar, does NasdaqMidlineSweepStrategy see a trade setup right now?" --
not "watch continuously and act." Re-run it periodically via an external
scheduler (cron, Task Scheduler) if recurring checks are wanted; no loop is
implemented here.

Usage:
    python live_signal_check.py --symbol USTEC --timeframe M5 --lookback-bars 1000
"""

import argparse
import sys
from pathlib import Path

# Add project root to python path so the script also works when invoked
# directly (python live_signal_check.py) rather than as a module.
sys.path.append(str(Path(__file__).parent.resolve()))

from application.services.market_state_builder import MarketStateBuilder
from core.models import Bar, Timeframe
from mt5.connector import MT5Connector
from strategy.diagnostics import top_rejection_reasons
from strategy.models import TradeSetup
from strategy.nasdaq_midline_sweep import NasdaqMidlineSweepStrategy
from strategy.strategy_engine import StrategyEngine
from utils.logging import setup_logger

logger = setup_logger("live_signal_check")

# Validated default from the USTEC out-of-sample backtest (106 trades, PF 1.0510
# -- see walkthrough.md). USTEC's measured density is ~173 M5 bars/trading day, so
# 1000 bars (~5-6 trading days) comfortably guarantees today's build session is
# included, gives the body-SMA (default sma_period=20) several days of warm-up
# beyond its own minimum, and gives the underlying swing/structure/SMC pipeline a
# reasonable amount of prior context (even though this strategy's own gates only
# read raw bars, not that pipeline's output).
DEFAULT_LOOKBACK_BARS = 1000
DEFAULT_BODY_MULTIPLIER = 1.5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parses command-line arguments for the signal check."""
    parser = argparse.ArgumentParser(
        description="Read-only, one-shot Midline Sweep live signal check (no orders placed)."
    )
    parser.add_argument("--symbol", default="USTEC", help="Trading instrument symbol (default: USTEC).")
    parser.add_argument("--timeframe", default="M5", help="Bar timeframe (default: M5).")
    parser.add_argument(
        "--lookback-bars",
        type=int,
        default=DEFAULT_LOOKBACK_BARS,
        help=f"Bars to fetch/replay before evaluating the final bar (default: {DEFAULT_LOOKBACK_BARS}).",
    )
    parser.add_argument(
        "--body-multiplier",
        type=float,
        default=DEFAULT_BODY_MULTIPLIER,
        help=f"NasdaqMidlineSweepStrategy body_multiplier (default: {DEFAULT_BODY_MULTIPLIER}, "
        "the validated USTEC OOS setting).",
    )
    return parser.parse_args(argv)


def check_signal(
    bars: list[Bar],
    symbol: str,
    timeframe: Timeframe,
    strategy: NasdaqMidlineSweepStrategy,
) -> tuple[TradeSetup | None, dict[str, dict], Bar]:
    """Replays `bars` through MarketStateBuilder + `strategy`, evaluating only the
    FINAL bar's outcome.

    All bars except the last are replayed normally (market state AND strategy
    both updated) so the strategy's own daily-scoped state (build-session
    midline/zone, trade-taken guard) is correctly built up -- this strategy is
    stateful across evaluate() calls, so skipping the replay would leave that
    state never initialized. Only the LAST bar's evaluate() outcome is reported.

    Diagnostics are reset (strategy.diagnostics.reset(), NOT strategy.reset() --
    the latter would also wipe the zone/trade-taken state just built up by the
    replay) immediately before the final bar, so the returned diagnostics
    reflect only that one evaluation, not an aggregate over the whole replay.

    Args:
        bars: Chronologically ordered (oldest first) bars, ending with the most
            recently closed bar.
        symbol: Trading instrument symbol.
        timeframe: Bar timeframe.
        strategy: A NasdaqMidlineSweepStrategy instance (constructed by the
            caller, so tests can inject a differently-configured instance).

    Returns:
        A tuple of (setup or None, diagnostics for the final bar only, the final bar).

    Raises:
        ValueError: If `bars` is empty.
    """
    if not bars:
        raise ValueError("No bars to evaluate.")

    state_builder = MarketStateBuilder(symbol=symbol, timeframe=timeframe)
    strategy_engine = StrategyEngine()
    strategy_engine.register_strategy(strategy)

    for bar in bars[:-1]:
        market_state = state_builder.append_bar(bar)
        strategy_engine.run(market_state)  # builds up daily zone/session state; result discarded

    strategy.diagnostics.reset()

    final_bar = bars[-1]
    market_state = state_builder.append_bar(final_bar)
    setups = strategy_engine.run(market_state)

    diagnostics = strategy_engine.get_diagnostics()
    return (setups[0] if setups else None), diagnostics, final_bar


def format_setup(setup: TradeSetup) -> str:
    """Formats a generated TradeSetup as a clear, human-readable block."""
    direction_label = "BUY (LONG)" if setup.direction.name == "BUY" else "SELL (SHORT)"
    lines = [
        "=" * 60,
        f"SIGNAL FOUND -- {setup.symbol} [{setup.timeframe.value}]",
        "=" * 60,
        f"Direction:      {direction_label}",
        f"Entry:          {setup.entry_zone[0]:.2f}",
        f"Stop-Loss:      {setup.stop_zone[0]:.2f}",
        f"Take-Profit:    {setup.target_zone[0]:.2f}",
        f"Trigger reason: {setup.trigger_reason}",
        f"Confidence:     {setup.confidence_score:.2f}",
        f"Bar timestamp:  {setup.timestamp}",
        f"Setup ID:       {setup.setup_id}",
        "=" * 60,
    ]
    return "\n".join(lines)


def format_no_signal(diagnostics: dict[str, dict], final_bar: Bar) -> str:
    """Formats a "no signal" result with the final bar's context and top rejection reason(s)."""
    reasons = top_rejection_reasons(diagnostics)
    reasons_str = ", ".join(f"{r} ({c})" for r, c in reasons) if reasons else "no diagnostics recorded"
    lines = [
        "-" * 60,
        "NO SIGNAL",
        "-" * 60,
        f"Final bar:      {final_bar.timestamp} | O={final_bar.open} H={final_bar.high} "
        f"L={final_bar.low} C={final_bar.close}",
        f"Rejection reason(s): {reasons_str}",
        "-" * 60,
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: connects (read-only), fetches recent bars, checks for a
    signal on the final bar, prints the result, and exits.
    """
    args = parse_args(argv)
    timeframe = Timeframe[args.timeframe]

    connector = MT5Connector()
    if not connector.connect():
        logger.error("Could not connect to the MT5 terminal.")
        sys.exit(1)

    try:
        bars = connector.fetch_recent_bars(args.symbol, args.timeframe, args.lookback_bars)
    finally:
        # Read-only, one-shot: disconnect immediately after the single fetch,
        # no session is held open.
        connector.disconnect()

    logger.info(
        "Fetched %d bar(s) for %s [%s]: %s -> %s",
        len(bars),
        args.symbol,
        args.timeframe,
        bars[0].timestamp,
        bars[-1].timestamp,
    )

    strategy = NasdaqMidlineSweepStrategy(body_multiplier=args.body_multiplier)
    setup, diagnostics, final_bar = check_signal(bars, args.symbol, timeframe, strategy)

    if setup is not None:
        print(format_setup(setup))
    else:
        print(format_no_signal(diagnostics, final_bar))


if __name__ == "__main__":
    main()
