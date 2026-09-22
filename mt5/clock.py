"""Does this machine's clock, and BROKER_TZ, still agree with the broker?

Two silent failures live behind this check, and neither shows up in any log today.

1. THE OS CLOCK. On 2026-09-21 at 07:24 UTC the VPS's system time jumped two hours
   forward -- a COM component (dllhost.exe) wrote the host's LOCAL wall clock into the
   guest's UTC -- and Windows Time pulled it back 2m04s later. Two polls wrote
   future-dated events into logs/trade_events.log and nothing else noticed. It landed at
   NY 03:24 and cost nothing. The same two minutes at 09:30 NY would have moved every
   time-based rule this repo has, and the only evidence would have been two odd lines in
   a log nobody reads during a session.

2. BROKER_TZ. mt5/rates.py relabels every MT5 bar from the broker's wall clock into real
   UTC, and the clock it uses is a claim about the broker (config/brokers.py SERVER_CLOCKS).
   Until 2026-09-22 that claim was Europe/Bucharest, and both brokers turned out to run
   New York close instead: the same UTC+2/+3, changing on the American dates. From
   2026-10-25, when Bucharest leaves DST a week before New York, every bar would have been
   stamped an hour late and the 09:30 opening range built from 08:30 bars. This check is
   what would have caught it -- by refusing every entry for that week.

Both are the same measurement: the broker's own wall clock, which MT5 reports raw in
every tick, against the wall clock BROKER_TZ predicts for right now. Agreement means the
OS clock AND the timezone are both right. A whole-hour disagreement means one of them is
not. Which one cannot be told apart from here -- but either is disqualifying, so this
does not need to.

Nothing here converts, corrects or trades. It measures and reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import MetaTrader5 as mt5  # noqa: N813

from mt5.rates import BROKER_TZ

# How far the broker's clock may sit from BROKER_TZ's prediction and still count as
# agreement. Ticks arrive continuously while a market is open, so in practice the gap is
# under a second; 120s absorbs a quiet minute on a thin symbol without hiding anything,
# because every failure this check exists for is a WHOLE HOUR or more.
TOLERANCE_SECONDS = 120

# Beyond this, a gap is the market being shut, not a clock. A real timezone error is 1-3
# hours and a clock jump of more than half a day would fail far louder things first; a
# weekend, meanwhile, leaves the last tick 24-72h behind, and whole-hour by coincidence.
MAX_PLAUSIBLE_HOURS = 12


@dataclass(frozen=True)
class ClockVerdict:
    """What the broker's clock says about ours."""

    drift: float | None  # seconds: the broker's wall clock minus BROKER_TZ's prediction
    hours_off: int       # that drift in whole hours; 0 when the two agree
    measurable: bool     # False when the last tick is too stale to judge (closed market)
    detail: str          # one line, ready to log or print

    @property
    def wrong(self) -> bool:
        """True only when the disagreement is real, whole-hour and unambiguous."""
        return self.measurable and self.hours_off != 0


def _verdict(drift: float, hours: int, ticker: str, offsets: str) -> ClockVerdict:
    if hours == 0:
        return ClockVerdict(drift, 0, True,
                            f"{ticker}: broker saatı BROKER_TZ ilə uyğundur ({offsets}, "
                            f"fərq {drift:+.1f}s)")
    return ClockVerdict(drift, hours, True,
                        f"{ticker}: broker saatı BROKER_TZ-dən {hours:+d} SAAT fərqlidir "
                        f"({offsets}, fərq {drift:+.1f}s). Ya bu maşının saatı sürüşüb, ya da "
                        f"{BROKER_TZ.key} artıq bu brokeri təsvir etmir "
                        f"(config/brokers.py SERVER_CLOCKS; müvəqqəti: .env MT5_BROKER_TZ, "
                        f"məs. America/New_York+7).")


def measure(ticker: str, now: datetime | None = None,
            tick_time: int | None = None) -> ClockVerdict:
    """Compares the broker's wall clock against the one BROKER_TZ predicts for `now`.

    Args:
        ticker: The broker's own symbol name, e.g. CFI's "XAUUSD_" -- the same string the
            bot polls, so a wrong ticker fails here rather than mid-session.
        now: Real UTC, defaulting to this machine's clock. That clock is half of what is
            under test, which is the point: a jump in it shows up as drift.
        tick_time: The raw MT5 tick epoch, for tests. Read from MT5 when not given. Raw
            on purpose -- rates_to_bars' own BROKER_TZ conversion would cancel the very
            error this looks for.

    Returns:
        A ClockVerdict. `wrong` is True only for a confident whole-hour disagreement.
    """
    now = now or datetime.now(UTC)
    if tick_time is None:
        tick = mt5.symbol_info_tick(ticker)
        if tick is None or not getattr(tick, "time", 0):
            return ClockVerdict(None, 0, False, f"{ticker}: MT5 tick vermədi, saat yoxlanmadı")
        tick_time = int(tick.time)

    # MT5's epoch reads as the broker's wall clock when labelled UTC -- see mt5/rates.py.
    server = datetime.fromtimestamp(tick_time, UTC).replace(tzinfo=None)
    expected = now.astimezone(BROKER_TZ).replace(tzinfo=None)
    drift = (server - expected).total_seconds()

    broker_offset = (server - now.replace(tzinfo=None)).total_seconds() / 3600
    tz = now.astimezone(BROKER_TZ).utcoffset()
    tz_offset = tz.total_seconds() / 3600 if tz is not None else 0.0
    offsets = f"broker UTC{broker_offset:+.0f}, {BROKER_TZ.key} UTC{tz_offset:+.0f}"

    if abs(drift) <= TOLERANCE_SECONDS:
        return _verdict(drift, 0, ticker, offsets)

    hours = round(drift / 3600)
    if hours and abs(hours) <= MAX_PLAUSIBLE_HOURS and abs(drift - hours * 3600) <= TOLERANCE_SECONDS:
        return _verdict(drift, hours, ticker, offsets)

    # Neither agreement nor a clean hour: the market is shut and the last tick is old.
    return ClockVerdict(drift, 0, False,
                        f"{ticker}: son tick {drift/3600:+.1f} saat köhnədir -- bazar bağlıdır, "
                        f"saat ölçülmədi")
