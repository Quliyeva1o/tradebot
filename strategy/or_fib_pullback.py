"""The 09:30-09:45 opening range, its break, and the golden-pocket pullback that trades it.

Rule, per NY trading day:
  - the first 15-minute candle (09:30-09:45) gives the opening range, High and Low;
  - the range must clear an ATR threshold, or the day is skipped as too quiet;
  - the break is the first M1 close beyond the range, and it fixes the side for the day;
  - the Fibonacci is drawn ON THE RANGE ITSELF, anchored at the extreme the break left:
        long  -- 0 = High, 1 = Low, level(f) = High - f * span
        short -- 0 = Low,  1 = High, level(f) = Low  + f * span
    so "61.8%" is the share of the range GIVEN BACK, the way a retracement tool reads
    it. The golden pocket is therefore DEEP INSIDE the range, below its midpoint;
  - entry is that 0.618 level, either as a resting limit or, with `require_rejection`,
    only once an M1 bar has closed back out of the pocket in the break's direction;
  - the stop is just beyond the 0.786 level, or at the range extreme the move started
    from (`stop_at_swing_extreme`);
  - the target is the morning extreme as of the bar before entry, a projection of the
    range (`target_extension_fib`, e.g. 1.272), or a fixed multiple of the trade's own
    risk (`target_r`, e.g. 1.0) -- the three are alternatives, and at most one is set;
  - nothing is entered after `entry_deadline`, and an open trade is closed at
    `close_at` -- the spec's "before momentum fades around midday".

`extension(f)` and `level(f)` are the same line read from opposite ends:
`level(f) == extension(1 - f)`. Both exist because the two names carry different
intent -- a retracement DEPTH versus a projection MULTIPLE -- and a caller that
reaches for `level(1.272)` to get a target would silently get a price on the wrong
side of the range.

This module owns the whole day's state machine, so a live runner and the backtest
step through the rule identically; only the exit loop is the backtest's own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from core.models import Bar, OrderType, SignalDirection
from core.validation import require_non_negative, require_positive
from execution.level_fill import limit_fill

NY = ZoneInfo("America/New_York")

# Travels as the order comment; MT5 keeps only the first 20 characters (see
# execution/mt5_broker.py), and no other bot's tag may be a prefix of this one.
STRATEGY_TAG = "setup_or_fib"


@dataclass(frozen=True)
class OrFibPullbackConfig:
    range_start: time = time(9, 30)  # inclusive
    range_end: time = time(9, 45)  # exclusive -- the first 15-minute candle
    entry_fib: float = 0.618  # the near edge of the golden pocket
    zone_fib: float = 0.786  # its far edge, and the anchor for the tight stop
    stop_at_swing_extreme: bool = False  # stop at the range extreme instead of beyond zone_fib
    stop_buffer_frac: float = 0.02  # of the range span: "just beyond" the 0.786 level
    target_extension_fib: float | None = None  # a projection of the range, e.g. 1.272
    target_r: float | None = None  # a fixed multiple of the trade's own risk
    require_rejection: bool = False  # wait for an M1 close back out of the pocket
    atr_period: int = 14
    atr_mult: float = 1.0  # range span must be >= this * ATR; 0 disables the filter
    entry_deadline: time = time(12, 0)
    close_at: time | None = time(12, 0)  # None holds the trade until the stop or target

    def __post_init__(self) -> None:
        require_positive(self.zone_fib - self.entry_fib, "zone_fib - entry_fib")
        require_positive(self.entry_fib, "entry_fib")
        require_non_negative(self.stop_buffer_frac, "stop_buffer_frac")
        require_non_negative(self.atr_mult, "atr_mult")
        require_positive(self.atr_period, "atr_period")
        if self.target_extension_fib is not None:
            require_positive(self.target_extension_fib - 1.0, "target_extension_fib - 1")
        if self.target_r is not None:
            require_positive(self.target_r, "target_r")
        # The three targets are alternatives, not layers: silently letting one win
        # would make a sweep row mean something other than its label says.
        if self.target_extension_fib is not None and self.target_r is not None:
            raise ValueError("target_extension_fib and target_r are alternatives; set at most one")
        # A cancel time before the entry deadline would close a trade at a price that
        # printed before it was even entered.
        if self.close_at is not None and self.close_at < self.entry_deadline:
            raise ValueError(
                f"close_at ({self.close_at}) must not precede entry_deadline ({self.entry_deadline})"
            )


@dataclass(frozen=True)
class OpeningRange:
    high: float
    low: float

    @property
    def span(self) -> float:
        return self.high - self.low

    def level(self, frac: float, *, long: bool) -> float:
        """The `frac` retracement of the range, measured back from the break's extreme."""
        return self.high - frac * self.span if long else self.low + frac * self.span

    def extension(self, frac: float, *, long: bool) -> float:
        """The `frac` projection of the range, measured from the break's origin."""
        return self.low + frac * self.span if long else self.high - frac * self.span


@dataclass(frozen=True)
class FibEntry:
    """The trade one day's setup calls for. Timestamps are UTC, like the bars."""

    direction: SignalDirection
    time: datetime
    price: float
    stop: float
    target: float
    break_time: datetime
    morning_extreme: float

    @property
    def risk(self) -> float:
        sign = 1 if self.direction == SignalDirection.BUY else -1
        return (self.price - self.stop) * sign


def _ny_minute(ts: datetime) -> int:
    local = ts.astimezone(NY)
    return local.hour * 60 + local.minute


def _minute_of(t: time) -> int:
    return t.hour * 60 + t.minute


def opening_range(day_bars: list[Bar], cfg: OrFibPullbackConfig) -> OpeningRange | None:
    """The day's 09:30-09:45 range, or None when the day cannot supply one.

    The bar stamped exactly at `range_start` must be there: without it the day's open
    print is missing and the range is some later, narrower window wearing the same name.
    Holidays and half days are how that shows up in the feed.
    """
    start, end = _minute_of(cfg.range_start), _minute_of(cfg.range_end)
    bars = [b for b in day_bars if start <= _ny_minute(b.timestamp) < end]
    if not bars or _ny_minute(bars[0].timestamp) != start:
        return None
    high, low = max(b.high for b in bars), min(b.low for b in bars)
    return OpeningRange(high=high, low=low) if high > low else None


def atr_ok(or_range: OpeningRange, atr: float | None, cfg: OrFibPullbackConfig) -> bool:
    """Whether the range clears the momentum threshold. A missing ATR fails the filter."""
    if cfg.atr_mult == 0:
        return True
    return atr is not None and or_range.span >= cfg.atr_mult * atr


class DayState:
    """One NY day's progress through the rule, fed that day's M1 bars in order.

    `on_bar` returns a FibEntry on the bar that completes the setup and None on every
    other bar, including every bar after the entry -- one trade per day, and the first
    break takes the day even if price later breaks the other way.

    The morning extreme that becomes the target is the one known BEFORE the entry bar
    opened: that bar's own high may well print after the entry filled.
    """

    def __init__(self, or_range: OpeningRange, cfg: OrFibPullbackConfig) -> None:
        self._or = or_range
        self._cfg = cfg
        self._long: bool | None = None
        self._break_time: datetime | None = None
        self._touched = False
        # Both extremes are tracked from the opening bar, because which one becomes the
        # target is not known until the break picks a side.
        self._high: float | None = None
        self._low: float | None = None
        self._done = False

    def on_bar(self, bar: Bar) -> FibEntry | None:
        if self._done:
            return None
        minute = _ny_minute(bar.timestamp)
        prior_high, prior_low = self._high, self._low
        self._track(bar)

        if minute < _minute_of(self._cfg.range_end):
            return None  # still inside the opening range
        if minute >= _minute_of(self._cfg.entry_deadline):
            self._done = True
            return None

        if self._long is None:
            # The break is a CLOSE beyond the range, so the earliest a trade can be
            # entered is the next bar -- this bar's close is the signal, not a fill.
            if bar.close > self._or.high:
                self._long, self._break_time = True, bar.timestamp
            elif bar.close < self._or.low:
                self._long, self._break_time = False, bar.timestamp
            return None

        entry = self._or.level(self._cfg.entry_fib, long=self._long)
        stop = self._stop()
        fill = self._rejection_fill(bar, entry, stop) if self._cfg.require_rejection \
            else limit_fill(OrderType.BUY_LIMIT if self._long else OrderType.SELL_LIMIT, entry, bar)
        if fill is None:
            return None

        prior_extreme = prior_high if self._long else prior_low
        target = self._target(prior_extreme, fill, stop)
        self._done = True
        sign = 1 if self._long else -1
        if (fill - stop) * sign <= 0 or (target - fill) * sign <= 0:
            return None  # a range this degenerate cannot be traded
        assert self._break_time is not None
        return FibEntry(
            direction=SignalDirection.BUY if self._long else SignalDirection.SELL,
            time=bar.timestamp, price=fill, stop=stop, target=target,
            break_time=self._break_time,
            morning_extreme=prior_extreme if prior_extreme is not None else fill,
        )

    def _track(self, bar: Bar) -> None:
        """The morning high and low, from the opening bar onwards."""
        self._high = bar.high if self._high is None else max(self._high, bar.high)
        self._low = bar.low if self._low is None else min(self._low, bar.low)

    def _stop(self) -> float:
        assert self._long is not None
        if self._cfg.stop_at_swing_extreme:
            return self._or.low if self._long else self._or.high
        zone = self._or.level(self._cfg.zone_fib, long=self._long)
        buffer = self._cfg.stop_buffer_frac * self._or.span
        return zone - buffer if self._long else zone + buffer

    def _target(self, prior_extreme: float | None, fill: float, stop: float) -> float:
        """Measured from the FILL, not the 0.618 level, so a `target_r` trade that
        reaches its target books exactly that many R gross."""
        assert self._long is not None
        if self._cfg.target_r is not None:
            sign = 1 if self._long else -1
            return fill + sign * self._cfg.target_r * abs(fill - stop)
        if self._cfg.target_extension_fib is not None:
            return self._or.extension(self._cfg.target_extension_fib, long=self._long)
        if prior_extreme is None:
            return self._or.high if self._long else self._or.low
        return prior_extreme

    def _rejection_fill(self, bar: Bar, entry: float, stop: float) -> float | None:
        """A market fill at the close of the bar that rejects the pocket, or None.

        The pocket must first be reached; the entry is then the first close back out of
        it in the break's direction. A bar that runs past the stop before any such close
        kills the setup -- the level that would have protected the trade was already gone.
        """
        assert self._long is not None
        if self._long:
            self._touched = self._touched or bar.low <= entry
            if self._touched and bar.low <= stop:
                self._done = True
                return None
            return bar.close if self._touched and bar.close > entry else None
        self._touched = self._touched or bar.high >= entry
        if self._touched and bar.high >= stop:
            self._done = True
            return None
        return bar.close if self._touched and bar.close < entry else None


def setup_id(symbol: str, entry: FibEntry) -> str:
    return f"{STRATEGY_TAG}_{symbol}_{entry.time.astimezone(NY):%Y%m%d}_{entry.direction.name}"
