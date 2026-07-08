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
        pass

    def check_mitigation(self, bars: Sequence[Bar], zones: list[Any]) -> list[Any]:
        """Determines if the price path has mitigated active zones.

        Handles list of OrderBlock or FairValueGap objects.

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
                for j in range(start_idx, n_bars):
                    bar = bars[j]
                    if zone.direction == OBDirection.BULLISH:
                        if bar.low <= zone.high:
                            is_mitigated = True
                            break
                    elif zone.direction == OBDirection.BEARISH:
                        if bar.high >= zone.low:
                            is_mitigated = True
                            break

            # Check if zone is a FairValueGap
            elif isinstance(zone, FairValueGap):
                start_idx = zone.end_index + 1
                for j in range(start_idx, n_bars):
                    bar = bars[j]
                    if zone.direction == FVGDirection.BULLISH:
                        if bar.low <= zone.upper_price:
                            is_mitigated = True
                            break
                    elif zone.direction == FVGDirection.BEARISH:
                        if bar.high >= zone.lower_price:
                            is_mitigated = True
                            break

            if is_mitigated:
                updated_zones.append(replace(zone, is_mitigated=True))
            else:
                updated_zones.append(zone)

        return updated_zones
