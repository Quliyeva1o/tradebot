"""The wall clock an MT5 server writes every timestamp in -- as a tzinfo.

MT5 stamps bars, ticks, deals, positions and order expiries with the broker SERVER's wall clock
encoded as if it were UTC (see mt5/rates.py). Turning those stamps into real instants needs the
server's clock as a timezone, and the usual one is not in the IANA database: most MT5 brokers keep
"New York close" time, New York's wall clock plus seven hours, so the trading day rolls over at
17:00 New York all year. It is UTC+2 in winter and UTC+3 in summer like Europe/Bucharest, but it
changes on the AMERICAN dates. The two disagree for about three weeks a year: from the second Sunday
of March to the last one, and from the last Sunday of October to the first Sunday of November.

This project assumed Europe/Bucharest from its first broker on, and on 2026-09-22 both accounts it
trades were measured to be New York + 7 instead (config/brokers.py has the evidence). No IANA key
names that clock, so it is built here: a real zone's wall clock shifted by whole hours.

It is a plain tzinfo, so everything the stdlib does with one works -- replace(tzinfo=...),
astimezone(), fromtimestamp(), fold for the repeated hour. pandas does NOT accept it: pandas 3
re-resolves a zone by its IANA key. Code that converts whole columns goes through `zone` and
`shift` instead, as backtest/live_replay/market.py does.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_SHIFTED = re.compile(r"(?P<zone>.+?)(?P<shift>[+-]\d{1,2})")


class BrokerClock(tzinfo):
    """A server's wall clock: `zone`'s wall clock moved by `shift_hours` whole hours.

    BrokerClock("America/New_York", 7) is New York close time. With no shift it behaves exactly
    like ZoneInfo(zone).
    """

    def __init__(self, zone: str, shift_hours: int = 0) -> None:
        super().__init__()
        self.zone = ZoneInfo(zone)
        self.shift_hours = int(shift_hours)
        self.shift = timedelta(hours=self.shift_hours)
        self.key = zone if not self.shift_hours else f"{zone}{self.shift_hours:+d}"

    @classmethod
    def parse(cls, text: str) -> BrokerClock:
        """"Europe/Bucharest" or "America/New_York+7" -- the form `key` prints and MT5_BROKER_TZ takes.

        A real IANA key wins over the shift reading, so "Etc/GMT+2" stays that zone (UTC-2, IANA's
        inverted sign) instead of becoming Etc/GMT shifted two hours the other way.

        Raises:
            ValueError: If `text` is neither a known zone nor a known zone plus whole hours.
        """
        text = text.strip()
        try:
            return cls(text)
        except (ZoneInfoNotFoundError, ValueError):
            pass
        match = _SHIFTED.fullmatch(text)
        if match is not None:
            try:
                return cls(match["zone"], int(match["shift"]))
            except (ZoneInfoNotFoundError, ValueError):
                pass
        raise ValueError(f"not a broker clock: {text!r} (expected e.g. 'America/New_York+7')")

    # --- tzinfo ------------------------------------------------------------------------------

    def _in_zone(self, dt: datetime) -> datetime:
        """`dt`'s server wall clock as the same reading of `zone`'s wall clock, fold kept."""
        return (dt.replace(tzinfo=None) - self.shift).replace(tzinfo=self.zone, fold=dt.fold)

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        if dt is None:
            return None
        offset = self._in_zone(dt).utcoffset()
        return None if offset is None else offset + self.shift

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None if dt is None else self._in_zone(dt).dst()

    def tzname(self, dt: datetime | None) -> str:
        offset = self.utcoffset(dt)
        if offset is None:
            return self.key
        hours, rest = divmod(int(offset.total_seconds()), 3600)
        return f"{hours:+03d}" if not rest else f"{hours:+03d}{rest // 60:02d}"

    def fromutc(self, dt: datetime) -> datetime:
        if dt.tzinfo is not self:
            raise ValueError("fromutc: dt.tzinfo is not self")
        local = self.zone.fromutc(dt.replace(tzinfo=self.zone))
        return (local.replace(tzinfo=None) + self.shift).replace(tzinfo=self, fold=local.fold)

    # --- identity ----------------------------------------------------------------------------

    def __reduce__(self) -> tuple:
        return (BrokerClock, (self.zone.key, self.shift_hours))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BrokerClock):
            return NotImplemented
        return (self.zone.key, self.shift_hours) == (other.zone.key, other.shift_hours)

    def __hash__(self) -> int:
        return hash((self.zone.key, self.shift_hours))

    def __repr__(self) -> str:
        return f"BrokerClock({self.key!r})"

    def __str__(self) -> str:
        return self.key


# New York close: what every broker this project has measured runs its server on.
NEW_YORK_CLOSE = BrokerClock("America/New_York", 7)
# What this project assumed until 2026-09-22. Kept for the pre-CFI history files in data/history/
# (FXTM era), whose server clock can no longer be measured -- see config/brokers.history_clock.
BUCHAREST = BrokerClock("Europe/Bucharest")
