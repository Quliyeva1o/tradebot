"""Mitigation zones monitoring module."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from core.models import Bar
from smc.fvg import FairValueGap, FVGDirection
from smc.order_block import OBDirection, OrderBlock


class MitigationMonitor:
    """Monitors previously identified zones (like OBs and FVGs) for mitigations (re-tests)."""

    def __init__(self) -> None:
        """Initializes the MitigationMonitor."""
        self._last_checked: dict[str, int] = {}

    def check_mitigation(self, bars: Sequence[Bar], zones: list[Any]) -> list[Any]:
        """Determines whether the price path has fully invalidated active zones.

        Handles list of OrderBlock or FairValueGap objects.

        Bug #22: a zone is only "mitigated" (invalidated) once a bar CLOSES
        all the way through it, past its far edge. A wick or close merely
        touching/entering the zone is a legitimate retest -- the intended
        entry trigger -- and must leave the zone active (is_mitigated stays
        False) so a strategy can still use it. The previous trigger
        (`bar.low <= zone.high` etc.) fired on the very first touch, which
        is mathematically indistinguishable from "price is inside the zone"
        (since bar.low <= bar.close always), making the zone permanently
        unusable at the exact moment a strategy would want to select it.

        Args:
            bars: Sequence of historical price candlestick Bar objects.
            zones: List of active structure zones (OrderBlock or FairValueGap).

        Returns:
            A list of updated zone objects with is_mitigated state synchronized.
        """
        updated_zones = []
        n_bars = len(bars)

        for zone in zones:
            if zone.is_mitigated:
                updated_zones.append(zone)
                continue

            is_mitigated = False

            # Check if zone is an OrderBlock
            if isinstance(zone, OrderBlock):
                start_idx = zone.bar_index + 1
                last_idx = self._last_checked.get(zone.id, start_idx - 1)
                start_search = max(start_idx, last_idx + 1)
                for j in range(start_search, n_bars):
                    bar = bars[j]
                    if zone.direction == OBDirection.BULLISH:
                        if bar.close < zone.low:
                            is_mitigated = True
                            break
                    elif zone.direction == OBDirection.BEARISH:
                        if bar.close > zone.high:
                            is_mitigated = True
                            break
                if not is_mitigated and n_bars > 0:
                    self._last_checked[zone.id] = n_bars - 1

            # Check if zone is a FairValueGap
            elif isinstance(zone, FairValueGap):
                start_idx = zone.end_index + 1
                last_idx = self._last_checked.get(zone.id, start_idx - 1)
                start_search = max(start_idx, last_idx + 1)
                for j in range(start_search, n_bars):
                    bar = bars[j]
                    if zone.direction == FVGDirection.BULLISH:
                        if bar.close < zone.lower_price:
                            is_mitigated = True
                            break
                    elif zone.direction == FVGDirection.BEARISH:
                        if bar.close > zone.upper_price:
                            is_mitigated = True
                            break
                if not is_mitigated and n_bars > 0:
                    self._last_checked[zone.id] = n_bars - 1

            if is_mitigated:
                updated_zones.append(replace(zone, is_mitigated=True))
                if zone.id in self._last_checked:
                    del self._last_checked[zone.id]
            else:
                updated_zones.append(zone)

        return updated_zones
