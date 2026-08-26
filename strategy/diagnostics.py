"""Rejection-reason diagnostics for trade setup strategies.

Tracks which gate inside a strategy's evaluate() rejected a candidate, so
that low-frequency setup generation can be root-caused (e.g. "trend filter
rejects 80% of bars" vs "FVG proximity rejects almost everything"). Counting
is a handful of dict increments per evaluate() call, negligible next to the
structural/SMC lookups already performed.
"""

from collections import Counter
from enum import Enum


class RejectionReason(str, Enum):
    """Enumerates the gate at which a strategy evaluation was rejected."""

    NO_TREND = "no_trend"
    NO_PREMIUM_DISCOUNT_ZONE = "no_premium_discount_zone"
    WRONG_ZONE = "wrong_zone"
    NO_BREAK_HISTORY = "no_break_history"
    LAST_BREAK_NOT_BOS = "last_break_not_bos"
    BREAK_WRONG_SWING_TYPE = "break_wrong_swing_type"
    NO_LATEST_BAR = "no_latest_bar"
    STALE_BREAK = "stale_break"
    NO_MATCHING_ORDER_BLOCK = "no_matching_order_block"
    NO_MATCHING_FVG = "no_matching_fvg"
    LIQUIDITY_NOT_SWEPT = "liquidity_not_swept"
    NO_DISPLACEMENT = "no_displacement"
    NON_POSITIVE_RISK = "non_positive_risk"
    RR_GATE_FAILED = "rr_gate_failed"
    DUPLICATE_SETUP = "duplicate_setup"

    # AccumulationBreakoutStrategy gates (5 Candle Accumulation Breakout Retest)
    NOT_IN_SESSION = "not_in_session"
    ACCUMULATION_NOT_READY = "accumulation_not_ready"
    NO_BREAKOUT = "no_breakout"
    NO_VOLUME_SPIKE = "no_volume_spike"
    NO_RETEST = "no_retest"
    TRADE_ALREADY_TAKEN = "trade_already_taken"

    # NasdaqMidlineSweepStrategy gates (NASDAQ Midline Sweep Strategy v2)
    # Reuses NO_DISPLACEMENT above for its own displacement/strong-move gate.
    ZONE_NOT_READY = "zone_not_ready"
    WRONG_SIDE_OF_MID = "wrong_side_of_mid"
    NO_SWEEP = "no_sweep"

    # OpeningRangeBreakoutStrategy gates (Strategy #3)
    # Reuses NO_BREAKOUT, NO_VOLUME_SPIKE, and TRADE_ALREADY_TAKEN above for its
    # own breakout/volume/one-trade-per-day gates (Bug #59: this used to have its
    # own TRADE_ALREADY_TAKEN_TODAY member, a duplicate of the identical
    # one-trade-per-day concept already used by AccumulationBreakoutStrategy/
    # ManipulationReversalStrategy/NasdaqMidlineSweepStrategy, which fragmented
    # cross-strategy diagnostics aggregation).
    RANGE_NOT_READY = "range_not_ready"
    WRONG_CLOSE_SIDE = "wrong_close_side"

    # OrderBlockRetestStrategy gates (Strategy #4)
    # Reuses NO_LATEST_BAR, NON_POSITIVE_RISK, RR_GATE_FAILED above.
    NO_ORDER_BLOCKS = "no_order_blocks"
    NO_TOUCH_DETECTED = "no_touch_detected"
    OB_ALREADY_USED = "ob_already_used"

    # ManipulationReversalStrategy gates (Strategy #5, "9:30 NY Manipulation Reversal")
    # Reuses NO_LATEST_BAR, NON_POSITIVE_RISK, TRADE_ALREADY_TAKEN above.
    REFERENCE_CANDLE_NOT_READY = "reference_candle_not_ready"
    NO_MANIPULATION_SCENARIO = "no_manipulation_scenario"
    TRAP_NOT_FROZEN = "trap_not_frozen"
    NO_REVERSAL_YET = "no_reversal_yet"

    # TrendVolumeConfirmationStrategy gates
    # Reuses NO_TREND, NO_LATEST_BAR, NO_VOLUME_SPIKE, and NON_POSITIVE_RISK
    # above for its own trend/volume/risk gates.
    NO_MAJOR_SWING_FOR_SL = "no_major_swing_for_sl"

    # SimpleLiquiditySweepStrategy gates (2-candle liquidity grab, no session filter)
    # Reuses NO_LATEST_BAR, NO_SWEEP, NON_POSITIVE_RISK, and RR_GATE_FAILED
    # above for its own bar-availability/sweep/risk gates.
    NO_PREV_BAR = "no_prev_bar"

    # SrDailyBiasStrategy gates (Support/Resistance + Daily Bias, liquidity-TP
    # variant -- pine scriptlerim/SR_Daily_Bias_Strategy.pine /
    # scripts/sr_daily_bias_backtest_liquidity_tp.py). Reuses NO_LATEST_BAR,
    # NON_POSITIVE_RISK above for its own bar-availability/risk gates.
    NO_DAILY_BIAS_YET = "no_daily_bias_yet"
    NEUTRAL_BIAS = "neutral_bias"
    WARMUP = "warmup"
    SR_TOO_CLOSE = "sr_too_close"
    STRONG_TREND_BLOCKS_BOUNCE = "strong_trend_blocks_bounce"
    NO_LIQUIDITY_TARGET = "no_liquidity_target"
    REWARD_TOO_SMALL = "reward_too_small"
    RISK_OUT_OF_BOUNDS = "risk_out_of_bounds"


class StrategyDiagnostics:
    """Counters tracking why a strategy accepted or rejected candidates."""

    def __init__(self) -> None:
        """Initializes empty counters."""
        self.evaluations: int = 0
        self.setups_generated: int = 0
        self.rejections: Counter[RejectionReason] = Counter()

    def record_evaluation(self) -> None:
        """Records that evaluate() was called once."""
        self.evaluations += 1

    def record_rejection(self, reason: RejectionReason) -> None:
        """Records a rejection at the given gate.

        Args:
            reason: The gate that rejected the candidate.
        """
        self.rejections[reason] += 1

    def record_setup_generated(self) -> None:
        """Records that a TradeSetup was successfully generated."""
        self.setups_generated += 1

    def reset(self) -> None:
        """Clears all counters."""
        self.evaluations = 0
        self.setups_generated = 0
        self.rejections.clear()

    def summary(self) -> dict[str, int | dict[str, int]]:
        """Returns a plain-dict snapshot suitable for logging/reporting.

        Returns:
            Dict with total evaluations, setups generated, and a rejection
            breakdown ordered from most to least common.
        """
        return {
            "evaluations": self.evaluations,
            "setups_generated": self.setups_generated,
            "rejections": {
                reason.value: count for reason, count in self.rejections.most_common()
            },
        }


def top_rejection_reasons(engine_diagnostics: dict[str, dict], top_n: int = 3) -> list[tuple[str, int]]:
    """Merges rejection counts across all strategies in a StrategyEngine.get_diagnostics() result.

    Args:
        engine_diagnostics: Output of StrategyEngine.get_diagnostics() (per-strategy summaries).
        top_n: Maximum number of (reason, count) pairs to return.

    Returns:
        The top_n most common rejection reasons across all strategies, descending by count.
    """
    merged: Counter[str] = Counter()
    for summary in engine_diagnostics.values():
        merged.update(summary.get("rejections", {}))
    return merged.most_common(top_n)
