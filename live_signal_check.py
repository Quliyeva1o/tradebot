#!/usr/bin/env python3
"""Read-only, one-shot live signal check for NasdaqMidlineSweepStrategy.

SAFETY -- THIS SCRIPT IS READ-ONLY WITH RESPECT TO MT5. It never calls
mt5.order_send, mt5.order_check, mt5.order_calc_margin, or any other MT5
trading/position-modifying API. It only calls MT5Connector.fetch_recent_bars()
(itself documented read-only -- see mt5/connector.py) and evaluates a strategy
against the fetched bars in memory. Safe to run against a live (non-demo)
account for signal inspection. The only outbound network call beyond MT5 is a
best-effort Telegram notification (see notifications/telegram.py) sent ONLY
when a signal is found -- it never places or affects any trade, and any
failure there is logged and swallowed, never raised (see send_telegram_alert).

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
import json
import sys
from pathlib import Path

# Add project root to python path so the script also works when invoked
# directly (python live_signal_check.py) rather than as a module.
sys.path.append(str(Path(__file__).parent.resolve()))

from application.services.market_state_builder import MarketStateBuilder
from config.settings import Settings
from core.models import Bar, SignalDirection, Timeframe
from mt5.connector import MT5Connector
from notifications.telegram import TelegramNotifier
from strategy.diagnostics import top_rejection_reasons
from strategy.models import TradeSetup
from strategy.nasdaq_midline_sweep import NasdaqMidlineSweepStrategy
from strategy.strategy_engine import StrategyEngine
from utils.logging import setup_logger

logger = setup_logger("live_signal_check", log_to_file=True)

# Validated default from the USTEC out-of-sample backtest (106 trades, PF 1.0510
# -- see walkthrough.md). USTEC's measured density is ~173 M5 bars/trading day, so
# 1000 bars (~5-6 trading days) comfortably guarantees today's build session is
# included, gives the body-SMA (default sma_period=20) several days of warm-up
# beyond its own minimum, and gives the underlying swing/structure/SMC pipeline a
# reasonable amount of prior context (even though this strategy's own gates only
# read raw bars, not that pipeline's output).
DEFAULT_LOOKBACK_BARS = 1000
DEFAULT_BODY_MULTIPLIER = 1.5

# Persisted across process invocations (this script is re-run from scratch by an
# external scheduler every few minutes -- see module docstring) so a duplicate
# Telegram alert isn't sent if two consecutive runs both evaluate the same "most
# recently closed" bar (e.g. broker feed latency around the scheduler interval).
# The one-trade-per-day guard inside NasdaqMidlineSweepStrategy itself is
# correctly re-derived every run via check_signal()'s replay, so it already
# prevents re-alerting on a DIFFERENT bar later the same day -- this file only
# covers the narrower case of the SAME bar being evaluated more than once.
STATE_FILE = Path(__file__).parent / "logs" / "last_signal_state.json"


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


def format_telegram_message(setup: TradeSetup) -> str:
    """Formats a TradeSetup as a Telegram alert: an emoji-marked header line
    (color-coded by direction) followed by plain-text fields.
    """
    emoji = "\U0001f7e2" if setup.direction == SignalDirection.BUY else "\U0001f534"  # green / red circle
    direction_label = "BUY (LONG)" if setup.direction == SignalDirection.BUY else "SELL (SHORT)"
    lines = [
        f"{emoji} MIDLINE SWEEP SIGNAL -- {setup.symbol} [{setup.timeframe.value}]",
        f"Direction: {direction_label}",
        f"Entry: {setup.entry_zone[0]:.2f}",
        f"Stop-Loss: {setup.stop_zone[0]:.2f}",
        f"Take-Profit: {setup.target_zone[0]:.2f}",
        f"Reason: {setup.trigger_reason}",
        f"Bar time: {setup.timestamp}",
    ]
    return "\n".join(lines)


def _signal_signature(setup: TradeSetup) -> str:
    """Builds a stable dedup key for a setup.

    Deliberately NOT setup.setup_id: that field embeds a fresh uuid4 (see
    NasdaqMidlineSweepStrategy.evaluate()) on every call, so two independent
    process runs evaluating the exact same closed bar would produce two
    TradeSetup objects with identical trading fields but different setup_ids
    -- an id-based comparison would never detect the duplicate. symbol +
    timeframe + direction + the setup's bar timestamp is stable and
    identical across repeated evaluations of the same bar.
    """
    return f"{setup.symbol}_{setup.timeframe.value}_{setup.direction.name}_{setup.timestamp.isoformat()}"


def _already_sent(signature: str) -> bool:
    """Whether `signature` matches the last-recorded sent signal.

    Fail-open: any read/parse failure (missing file, corrupt JSON, race
    with a concurrent write) is treated as "not sent yet" -- a missed
    dedup is a harmless duplicate Telegram message, but treating a broken
    state file as "already sent" could silently suppress a real, new
    signal. Same fail-safe direction as send_telegram_alert's own
    never-raise contract.
    """
    try:
        return json.loads(STATE_FILE.read_text()).get("last_signature") == signature
    except Exception:  # noqa: BLE001 - dedup state is best-effort, must never block a real alert
        return False


def _record_sent(signature: str) -> None:
    """Persists `signature` as the last-sent signal. Best-effort, never raises."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"last_signature": signature}))
    except Exception as exc:  # noqa: BLE001 - dedup state is best-effort, must never block the caller
        logger.warning("Could not persist signal state: %s", type(exc).__name__)


def send_telegram_alert(setup: TradeSetup) -> None:
    """Best-effort Telegram notification for a found signal.

    Never raises: this is a supplementary notification channel, and a
    misconfigured or unreachable Telegram integration must never affect the
    signal check's own exit status or console output. Any failure (missing
    credentials, network/HTTP error, or anything else) is logged and
    swallowed here.
    """
    try:
        settings = Settings.load()
        notifier = TelegramNotifier(settings.TELEGRAM_TOKEN, settings.TELEGRAM_CHAT_ID)
        notifier.send_message(format_telegram_message(setup))
    except Exception as exc:  # noqa: BLE001 - notification is best-effort, must never affect the caller
        logger.warning("Telegram alert failed: %s", type(exc).__name__)


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
        signature = _signal_signature(setup)
        if _already_sent(signature):
            logger.info(
                "RESULT: SIGNAL %s %s @ %s (duplicate of last-sent bar -- Telegram alert suppressed)",
                setup.symbol,
                setup.direction.name,
                setup.timestamp,
            )
        else:
            send_telegram_alert(setup)
            _record_sent(signature)
            logger.info(
                "RESULT: SIGNAL %s %s @ %s (Telegram alert sent)",
                setup.symbol,
                setup.direction.name,
                setup.timestamp,
            )
    else:
        print(format_no_signal(diagnostics, final_bar))
        reasons = top_rejection_reasons(diagnostics)
        reasons_str = ", ".join(f"{r} ({c})" for r, c in reasons) if reasons else "no diagnostics recorded"
        logger.info("RESULT: NO SIGNAL (top reason: %s)", reasons_str)


if __name__ == "__main__":
    main()
