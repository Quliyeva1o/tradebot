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

from datetime import UTC, datetime

import MetaTrader5 as mt5  # noqa: N813

from core.models import Bar

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
    """
    return [
        Bar(
            timestamp=datetime.fromtimestamp(int(row["time"]), tz=UTC),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["tick_volume"]),
            spread=float(row["spread"]) * point,
        )
        for row in rates
    ]
