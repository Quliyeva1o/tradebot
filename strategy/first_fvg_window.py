"""The first 3-candle FVG inside a New York time window -- the "10:00 First FVG" rule.

Walk-forward validated on FundingPips NDX100, 2026-09-15 (24-month train / 6-month
validation, 45-config grid per rule): at M15 with the stop on candle 1 and a 3R target
it was green in 7 of 9 folds, full-history PF 1.154 (n=726), last-year PF 1.164. The
unbiased selection-test estimate is lower, PF ~1.10 out of sample. The same rule on M1
is net-negative in every year 2020-2025 and green in 1 of 9 folds.

Rule, per NY trading day:
  - the day must have a bar stamped exactly at `session_start`;
  - candle 1 may start `c1_bars_before` bars before it, so with the defaults the
    10:00 candle can be the middle one;
  - the first classic gap wins: candle 3's low above candle 1's high (long) or its
    high below candle 1's low (short); later gaps that day are ignored;
  - candle 3 must start before `third_candle_before`, otherwise the day has no setup;
  - entry is a limit at the gap's near edge, working from candle 3's close;
  - the stop is candle 1's low (long) or high (short), the target `tp_r` times the risk.

Deliberately separate from strategy/first_fvg_15m.py: that class is the 09:30 rule
with a body stop and a 2R target, detects the touch on a closed bar and enters at
market. This one only describes the order; the broker works the limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.models import Bar, SignalDirection
from core.validation import require_non_negative, require_positive

NY = ZoneInfo("America/New_York")

# Every setup id starts with this, and the id travels as the order comment. MT5 keeps
# only the first 20 characters of a long comment (see execution/mt5_broker.py), so the
# tag must stay within 20 to keep positions attributable. It must also not be a prefix
# of another bot's tag: "setup_first_fvg" would claim first_fvg_15m's positions.
STRATEGY_TAG = "setup_fvg_window"


@dataclass(frozen=True)
class FirstFvgWindowConfig:
    session_start: time = time(10, 0)
    c1_bars_before: int = 1
    third_candle_before: time = time(11, 0)
    tp_r: float = 3.0
    bar_minutes: int = 15

    def __post_init__(self) -> None:
        require_positive(self.tp_r, "tp_r")
        require_positive(self.bar_minutes, "bar_minutes")
        require_non_negative(self.c1_bars_before, "c1_bars_before")


@dataclass(frozen=True)
class FvgPlan:
    """The limit order one day's setup calls for. Timestamps are UTC, like the bars."""

    direction: SignalDirection
    c1_time: datetime
    confirm_close: datetime
    entry: float
    stop: float
    target: float


def _ny_minute(ts: datetime) -> int:
    local = ts.astimezone(NY)
    return local.hour * 60 + local.minute


def find_plan(day_bars: list[Bar], cfg: FirstFvgWindowConfig) -> FvgPlan | None:
    """The day's setup, or None. `day_bars` must hold ONE NY date's bars, oldest first."""
    start = cfg.session_start.hour * 60 + cfg.session_start.minute
    if not any(_ny_minute(b.timestamp) == start for b in day_bars):
        return None

    first_c1 = start - cfg.c1_bars_before * cfg.bar_minutes
    last_third = cfg.third_candle_before.hour * 60 + cfg.third_candle_before.minute
    session = [b for b in day_bars if _ny_minute(b.timestamp) >= first_c1]

    for c1, c3 in zip(session, session[2:]):
        if _ny_minute(c3.timestamp) >= last_third:
            return None
        if c3.low > c1.high:
            direction, entry, stop = SignalDirection.BUY, c3.low, c1.low
        elif c3.high < c1.low:
            direction, entry, stop = SignalDirection.SELL, c3.high, c1.high
        else:
            continue
        sign = 1 if direction == SignalDirection.BUY else -1
        risk = (entry - stop) * sign
        if risk <= 0:
            return None
        return FvgPlan(
            direction=direction,
            c1_time=c1.timestamp,
            confirm_close=c3.timestamp + timedelta(minutes=cfg.bar_minutes),
            entry=entry,
            stop=stop,
            target=entry + sign * cfg.tp_r * risk,
        )
    return None


def order_expires_at(plan: FvgPlan) -> datetime:
    """The limit works until New York midnight after its setup's date, in UTC."""
    next_day = plan.c1_time.astimezone(NY).date() + timedelta(days=1)
    return datetime.combine(next_day, time(0, 0), tzinfo=NY).astimezone(UTC)


def setup_id(symbol: str, plan: FvgPlan) -> str:
    return f"{STRATEGY_TAG}_{symbol}_{plan.c1_time.astimezone(NY):%Y%m%d}_{plan.direction.name}"
