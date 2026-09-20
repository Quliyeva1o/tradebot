"""The 09:30-10:00 New York range, its Fibonacci midline, and the retest that trades it.

Rule, per NY trading day:
  - the 09:30-10:00 M1 bars give a range High and Low; the Fibonacci is drawn across it
    IN THE DIRECTION OF THE BIAS, so 0 sits at the end the move is leaving:
        long  -- 0 = Low,  1 = High, level(f) = Low  + f * (High - Low)
        short -- 0 = High, 1 = Low,  level(f) = High - f * (High - Low)
    Both readings put 0.5 on the same midline; only 0.236 and 1.618 flip sides, which
    is what keeps the stop behind the entry and the target beyond the range for BOTH
    directions. Anchoring the fib one way for both would put a long's 0.236 stop ABOVE
    its entry, so this flip is the only coherent reading of the rule.
  - the price at `bias_at` decides the side: above the 0.5 midline is long, below is
    short. That price is the close of the M1 bar ENDING at `bias_at` -- with the
    defaults, the 10:00 bar, whose close prints at 10:01.
  - entry is a limit at 0.5. The bias guarantees price is already on the far side of
    it, so the order is a genuine retest: it can only fill by coming back.
  - the stop is 0.236 and the target 1.618, which fixes the reward at
    (1.618 - 0.5) / (0.5 - 0.236) = 4.23R regardless of how wide the range was.

"Price must STAY beyond 0.5 until the retest" needs no separate check: the first touch
of 0.5 IS the entry, so the order cannot survive a move through the midline.

This module only describes the order; the broker (or the backtest) works the limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.models import Bar, SignalDirection
from core.validation import require_positive

NY = ZoneInfo("America/New_York")

# Travels as the order comment; MT5 keeps only the first 20 characters (see
# execution/mt5_broker.py), and no other bot's tag may be a prefix of this one.
STRATEGY_TAG = "setup_ny_fib"


@dataclass(frozen=True)
class NyFibRetestConfig:
    range_start: time = time(9, 30)  # inclusive
    range_end: time = time(10, 0)  # exclusive
    bias_at: time = time(10, 1)  # the close that decides the side prints here
    entry_fib: float = 0.5
    stop_fib: float = 0.236
    target_fib: float = 1.618
    order_expires: time = time(16, 0)  # unfilled limit is cancelled at this NY time

    def __post_init__(self) -> None:
        require_positive(self.target_fib - self.entry_fib, "target_fib - entry_fib")
        require_positive(self.entry_fib - self.stop_fib, "entry_fib - stop_fib")

    @property
    def reward_r(self) -> float:
        """Reward per unit of risk, fixed by the three levels alone."""
        return (self.target_fib - self.entry_fib) / (self.entry_fib - self.stop_fib)


@dataclass(frozen=True)
class FibPlan:
    """The limit order one day's setup calls for. Timestamps are UTC, like the bars."""

    direction: SignalDirection
    bias_time: datetime
    bias_price: float
    range_high: float
    range_low: float
    entry: float
    stop: float
    target: float

    @property
    def risk(self) -> float:
        sign = 1 if self.direction == SignalDirection.BUY else -1
        return (self.entry - self.stop) * sign


def fib_level(high: float, low: float, frac: float, *, long: bool) -> float:
    """The `frac` Fibonacci level of a range measured in the bias's direction."""
    span = high - low
    return low + frac * span if long else high - frac * span


def _ny_minute(ts: datetime) -> int:
    local = ts.astimezone(NY)
    return local.hour * 60 + local.minute


def _minute_of(t: time) -> int:
    return t.hour * 60 + t.minute


def find_plan(day_bars: list[Bar], cfg: NyFibRetestConfig) -> FibPlan | None:
    """The day's setup, or None.

    `day_bars` must hold ONE NY date's M1 bars, oldest first. Only the bars up to
    `bias_at` are read, so a caller may pass just the morning slice.
    """
    start, end, bias = _minute_of(cfg.range_start), _minute_of(cfg.range_end), _minute_of(cfg.bias_at)

    range_bars = [b for b in day_bars if start <= _ny_minute(b.timestamp) < end]
    # The opening bar must be there: without it the day's 09:30 print is missing and
    # the range is some later, narrower window wearing the same name (holidays and
    # half days are how this shows up in the feed).
    if not range_bars or _ny_minute(range_bars[0].timestamp) != start:
        return None

    # The bias bar is the one that CLOSES at bias_at, i.e. stamped one minute earlier.
    # Falling back to an older bar would read the bias off a stale price.
    bias_bar = next((b for b in day_bars if _ny_minute(b.timestamp) == bias - 1), None)
    if bias_bar is None:
        return None

    high = max(b.high for b in range_bars)
    low = min(b.low for b in range_bars)
    if high <= low:
        return None

    midline = fib_level(high, low, cfg.entry_fib, long=True)
    if bias_bar.close == midline:
        return None  # no side to take
    long = bias_bar.close > midline

    return FibPlan(
        direction=SignalDirection.BUY if long else SignalDirection.SELL,
        bias_time=bias_bar.timestamp + timedelta(minutes=1),
        bias_price=bias_bar.close,
        range_high=high,
        range_low=low,
        entry=midline,
        stop=fib_level(high, low, cfg.stop_fib, long=long),
        target=fib_level(high, low, cfg.target_fib, long=long),
    )


def order_expires_at(plan: FibPlan, cfg: NyFibRetestConfig) -> datetime:
    """The NY wall-clock time the unfilled limit is cancelled, in UTC."""
    day = plan.bias_time.astimezone(NY).date()
    return datetime.combine(day, cfg.order_expires, tzinfo=NY).astimezone(UTC)


def setup_id(symbol: str, plan: FibPlan) -> str:
    return f"{STRATEGY_TAG}_{symbol}_{plan.bias_time.astimezone(NY):%Y%m%d}_{plan.direction.name}"
