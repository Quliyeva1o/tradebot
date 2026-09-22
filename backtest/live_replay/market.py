"""Bars and FX rates the replay trades on.

The history CSVs hold BID bars whose "time" column is the broker's server clock (New York close
for both brokers -- config/brokers.py SERVER_CLOCKS -- see data/download_history.py's
write_bars_csv) and whose "spread" column is already in price units (mt5/rates.py multiplies MT5's
integer points by the symbol's point size). Everything here works in genuine UTC epoch seconds,
which is what Bar.timestamp carries live; a BarFrame keeps the clock its file was written in for
the few things that happen on the server's clock (swap at server midnight, the Friday cutoff).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from config.brokers import SERVER_CLOCKS, history_clock
from core.broker_clock import BrokerClock
from core.models import Bar

NY = ZoneInfo("America/New_York")
DEFAULT_DATA_DIR = Path("data/history/fundingpips")
DEFAULT_CLOCK = SERVER_CLOCKS["fundingpips"]  # DEFAULT_DATA_DIR's


@dataclass
class BarFrame:
    """Bid OHLC as arrays; `ts` is each bar's OPEN in UTC epoch seconds."""

    symbol: str
    minutes: int
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    spread: np.ndarray
    clock: BrokerClock = DEFAULT_CLOCK  # the server clock the source file was written in
    ts_close: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self.ts_close = self.ts + self.minutes * 60

    def __len__(self) -> int:
        return int(len(self.ts))

    def bar(self, i: int) -> Bar:
        return Bar(timestamp=datetime.fromtimestamp(int(self.ts[i]), UTC), open=float(self.open[i]),
                   high=float(self.high[i]), low=float(self.low[i]), close=float(self.close[i]),
                   volume=0.0, spread=float(self.spread[i]))

    def count_closed_by(self, epoch: int) -> int:
        """How many bars have closed at or before `epoch` -- a bar is invisible until it closes."""
        return int(np.searchsorted(self.ts_close, epoch, side="right"))


def server_wall_to_utc(naive: pd.Series, clock: BrokerClock) -> pd.Series:
    """Naive server wall-clock readings as real UTC instants.

    pandas cannot localize to a BrokerClock (it re-resolves zones by IANA key), so the reading is
    moved into the clock's real zone first: New York close 16:30 is New York 09:30. The autumn hour
    that occurs twice resolves to the first (summer-time) reading, matching
    scripts/backtest_common.load_m1's datetime.replace(tzinfo=...) (fold=0).
    """
    in_zone = naive - pd.Timedelta(hours=clock.shift_hours)
    return in_zone.dt.tz_localize(clock.zone, ambiguous=np.ones(len(naive), dtype=bool),
                                  nonexistent="shift_forward").dt.tz_convert("UTC")


def server_wall(ts: np.ndarray, clock: BrokerClock) -> pd.DatetimeIndex:
    """UTC epoch seconds as naive server wall-clock readings -- the inverse of server_wall_to_utc."""
    utc = pd.DatetimeIndex(pd.to_datetime(ts, unit="s", utc=True))
    return utc.tz_convert(clock.zone).tz_localize(None) + pd.Timedelta(hours=clock.shift_hours)


def load_bars(path: Path, symbol: str, minutes: int, start: datetime | None = None,
              end: datetime | None = None, clock: BrokerClock | None = None) -> BarFrame:
    """Reads one history CSV into a BarFrame.

    `clock` is the server clock the file was written in; by default it is read from the broker
    folder the file sits in (config/brokers.history_clock).
    """
    clock = clock if clock is not None else history_clock(path)
    df = pd.read_csv(path)
    naive = pd.to_datetime(df["time"], format="%Y-%m-%d %H:%M:%S")
    local = server_wall_to_utc(naive, clock)
    # pandas 3 keeps microsecond units; dividing Timedeltas avoids any unit assumption.
    epoch = ((local - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)).to_numpy(dtype=np.int64)
    order = np.argsort(epoch, kind="stable")
    epoch = epoch[order]
    mask = np.r_[True, epoch[1:] != epoch[:-1]]
    if start is not None:
        mask &= epoch >= int(start.timestamp())
    if end is not None:
        mask &= epoch < int(end.timestamp())
    values = {col: df[col].to_numpy(dtype=float)[order][mask] for col in ("open", "high", "low", "close")}
    spread = (df["spread"].fillna(0.0).to_numpy(dtype=float)[order][mask] if "spread" in df
              else np.zeros(int(mask.sum())))
    return BarFrame(symbol=symbol, minutes=minutes, ts=epoch[mask], spread=spread, clock=clock, **values)


def load_m1(symbol: str, data_dir: Path = DEFAULT_DATA_DIR, start: datetime | None = None,
            end: datetime | None = None) -> BarFrame:
    return load_bars(Path(data_dir) / f"{symbol}_M1.csv", symbol, 1, start, end)


def trim_to_real_m1(m1: BarFrame, min_bars: int = 200_000) -> BarFrame:
    """`m1` from the first year of the unbroken run of real minute data that ends it.

    A broker can pad years it holds no M1 for with a few hundred coarse rows -- CFI's gold has
    ~520 a year for 2009-2015 and 4k in 2016 -- so a file's first timestamp claims history it does
    not have, and a replay over it trades on bars that are not minutes (75 phantom trades worth
    +32R in CFI's 2016). A full 24/5 year of M1 is ~360k bars; a year with fewer than `min_bars`
    is not treated as M1. The last year is never held against the file: it is still being written.
    """
    years = pd.DatetimeIndex(pd.to_datetime(m1.ts, unit="s", utc=True)).year.to_numpy()
    counts = pd.Series(years).value_counts().sort_index()
    start = int(counts.index[-1])
    for year, n in reversed(list(counts.items())[:-1]):
        if n < min_bars:
            break
        start = int(year)
    i = int(np.searchsorted(m1.ts, int(datetime(start, 1, 1, tzinfo=UTC).timestamp())))
    return BarFrame(symbol=m1.symbol, minutes=m1.minutes, ts=m1.ts[i:], open=m1.open[i:],
                    high=m1.high[i:], low=m1.low[i:], close=m1.close[i:], spread=m1.spread[i:],
                    clock=m1.clock)


def aggregate(m1: BarFrame, minutes: int) -> BarFrame:
    """Builds M5/M15 the way MT5 does: clock-aligned buckets labelled by their open."""
    if minutes == m1.minutes:
        return m1
    bucket = m1.ts - m1.ts % (minutes * 60)
    starts = np.flatnonzero(np.r_[True, bucket[1:] != bucket[:-1]])
    ends = np.r_[starts[1:], len(bucket)] - 1
    return BarFrame(symbol=m1.symbol, minutes=minutes, ts=bucket[starts], open=m1.open[starts],
                    high=np.maximum.reduceat(m1.high, starts), low=np.minimum.reduceat(m1.low, starts),
                    close=m1.close[ends], spread=m1.spread[starts], clock=m1.clock)


def frame_from_bars(symbol: str, minutes: int, bars: Sequence[Bar],
                    clock: BrokerClock = DEFAULT_CLOCK) -> BarFrame:
    """A BarFrame from Bar objects -- for tests and for feeding hand-built sessions."""
    return BarFrame(symbol=symbol, minutes=minutes,
                    ts=np.array([int(b.timestamp.timestamp()) for b in bars], dtype=np.int64),
                    open=np.array([b.open for b in bars], dtype=float),
                    high=np.array([b.high for b in bars], dtype=float),
                    low=np.array([b.low for b in bars], dtype=float),
                    close=np.array([b.close for b in bars], dtype=float),
                    spread=np.array([b.spread for b in bars], dtype=float), clock=clock)


@dataclass(frozen=True)
class FxSeries:
    """USD value of one unit of a symbol's profit currency, from daily closes."""

    currency: str
    ts: np.ndarray | None
    usd: np.ndarray | None

    def usd_per_unit(self, epoch: int) -> float:
        if self.currency == "USD":
            return 1.0
        # -2: the bar containing `epoch` has not closed yet, so use the previous day's close.
        i = max(int(np.searchsorted(self.ts, epoch, side="right")) - 2, 0)
        return float(self.usd[i])


def load_fx(currency: str, data_dir: Path = DEFAULT_DATA_DIR) -> FxSeries:
    if currency == "USD":
        return FxSeries("USD", None, None)
    pair, invert = {"EUR": ("EURUSD", False), "JPY": ("USDJPY", True)}[currency]
    path = Path(data_dir) / f"{pair}_D1.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing; run: python -m data.download_history --symbols {pair} "
            f"--timeframe D1 --start 2020-01-01 --output-dir {data_dir}"
        )
    frame = load_bars(path, pair, 1440)
    return FxSeries(currency, frame.ts, 1.0 / frame.close if invert else frame.close)
