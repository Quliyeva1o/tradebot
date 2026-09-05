"""Shared MT5 raw-rate/timeframe utilities.

data/download_history.py, mt5/history_downloader.py, and mt5/connector.py all
need to (a) map a timeframe key (e.g. "M5") to MT5's timeframe constant, (b)
resolve a symbol's point size to correctly scale MT5's raw integer `spread`
(points) into a price-space value, and (c) convert MT5's copy_rates_*() numpy
record arrays into Bar objects. This is the single source of truth for all
three, kept in its own leaf module (importing nothing project-internal besides
core.models) specifically to avoid a circular import: mt5/history_downloader.py
and data/download_history.py both already import MT5Connector from
mt5/connector.py, so neither of those modules -- nor mt5/connector.py itself --
can be the shared home without creating a cycle.
"""

import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5  # noqa: N813
from dotenv import load_dotenv

from core.models import Bar

# Idempotent (python-dotenv never overwrites an already-set env var), and
# needed here specifically: this module is imported by mt5/connector.py
# before MT5Connector.connect() gets a chance to call load_dotenv() itself,
# so MT5_BROKER_TZ below would silently miss a real .env file otherwise.
load_dotenv()

# MT5's copy_rates_*() `time` field is a Unix-epoch integer whose calendar
# value equals the BROKER SERVER's wall clock, not true UTC -- e.g. when
# real-world UTC is 07:23, MT5 reports an epoch that reads as 10:23 if you
# (wrongly) label it UTC. Every live strategy that derives an NY/London/etc.
# session window via `bar.timestamp.astimezone(<session_tz>)` (see
# strategy/midnight_fvg.py, strategy/ny_open_accumulation_breakout.py,
# strategy/manipulation_reversal.py, strategy/nasdaq_midline_sweep.py,
# strategy/opening_range_breakout.py) silently depends on Bar.timestamp
# actually BEING true UTC -- so rates_to_bars() must apply this correction
# (interpret the raw epoch as broker wall-clock, then convert to real UTC)
# rather than passing the mislabeled epoch through. data/download_history.py's
# write_bars_csv() converts back to broker time before writing the naive CSV
# string specifically to keep this fix invisible to the file format -- see
# that function's docstring.
#
# "Europe/Bucharest" was empirically confirmed for ForexTimeFXTM-Demo02, the
# account used when this constant was first hardcoded -- it is NOT a
# property of MT5 in general, and has never been confirmed for any other
# broker/account this project has since connected to (e.g. HFM, or
# FundingPips-Trial). Connecting under a different MT5_LOGIN/MT5_SERVER
# without re-verifying this would silently shift every session-boundary
# comparison project-wide. MT5_BROKER_TZ lets an operator override it per
# .env without a code change; unset, behavior is unchanged (Bucharest).
BROKER_TZ = ZoneInfo(os.getenv("MT5_BROKER_TZ", "Europe/Bucharest"))

TIMEFRAME_MAPPING = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


def get_symbol_point(symbol: str) -> float:
    """Fetches the symbol's point size (smallest price increment) from MT5.

    MT5's `copy_rates_*` results report `spread` as an integer number of
    points, not a price-space value -- it must be multiplied by this point
    size to become a price-space spread usable by BacktestEngine. Point size
    varies by instrument (e.g. 0.00001 for EURUSD, 0.01 for an index like
    USTEC), so it cannot be assumed/hardcoded.

    Raises:
        RuntimeError: If symbol_info is unavailable for the (already-selected) symbol.
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"symbol_info unavailable for {symbol}; cannot resolve point size.")
    return float(info.point)


def rates_to_bars(rates: object, point: float) -> list[Bar]:
    """Converts an MT5 copy_rates_*() numpy record array into a Bar list.

    Args:
        rates: Raw MT5 rate rows.
        point: The symbol's point size, used to convert the raw integer
            `spread` (points) into a price-space value (points * point).

    Returns:
        Bars whose `timestamp` is genuine UTC -- see BROKER_TZ's module-level
        comment for why this requires re-labeling MT5's raw epoch (broker
        wall-clock) before converting, not just tagging it UTC.
    """
    bars: list[Bar] = []
    prev_utc: datetime | None = None
    for row in rates:
        naive = datetime.fromtimestamp(int(row["time"]), tz=UTC).replace(tzinfo=None)
        fold0 = naive.replace(tzinfo=BROKER_TZ, fold=0).astimezone(UTC)
        fold1 = naive.replace(tzinfo=BROKER_TZ, fold=1).astimezone(UTC)
        if fold0 == fold1 or prev_utc is None:
            timestamp = fold0
        else:
            # Ambiguous broker-local wall-clock (the repeated hour during
            # BROKER_TZ's autumn DST fall-back, e.g. Bucharest's 03:00-03:59
            # occurring twice as EEST then EET): Python's fold=0 default
            # always resolves to the PRE-transition (larger UTC offset)
            # occurrence, which is wrong for the second, real occurrence of
            # that hour. Disambiguate using bar order instead -- every
            # caller of fetch_recent_bars() assumes strictly increasing
            # timestamps at a fixed cadence, so pick whichever candidate
            # continues that instead of jumping backwards.
            timestamp = fold0 if fold0 > prev_utc else fold1
        prev_utc = timestamp
        bars.append(
            Bar(
                timestamp=timestamp,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["tick_volume"]),
                spread=float(row["spread"]) * point,
            )
        )
    return bars
