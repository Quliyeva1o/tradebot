"""Order block tracker ported from the Pine Script indicator
'ICT MTF Order Block Wicks [MK]' (tradebot/pine scriptlerim/ICT_MTF_Order_Block_Wicks.pine).

Reproduces the indicator's own box-array bookkeeping bar-for-bar so the
zones a strategy trades against are the same zones the indicator would be
showing on the chart at that moment -- formation condition, top-only
duplicate check, max-active eviction (including the original script's
evict-before-dedup-check quirk), and "Normal"/"Wicks" mitigation deletion.
The indicator only ever runs its "Body" detection branch in practice
(fvgmethod is hardcoded true in the source), so that is the only pattern
implemented here.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from core.models import Bar


class PineOBDirection(Enum):
    """Direction of a Pine-detected order block."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


@dataclass
class PineOrderBlock:
    """A single OB zone, mirroring the Pine box's own top/bottom fields.

    For a bullish OB: top = prior candle's high (the entry/"incursion" edge),
    bottom = prior candle's open (the "Normal" mitigation edge).
    For a bearish OB: top = prior candle's open (mitigation edge),
    bottom = prior candle's low (entry edge).
    """

    id: str
    bar_index: int
    top: float
    bottom: float
    direction: PineOBDirection
    timestamp: datetime


class PineOrderBlockTracker:
    """Reproduces the indicator's per-timeframe bull/bear box arrays.

    Call update() once per newly closed bar, in chronological order.
    """

    def __init__(self, max_active: int = 8) -> None:
        self.max_active = max_active
        self.bull_obs: list[PineOrderBlock] = []
        self.bear_obs: list[PineOrderBlock] = []
        self._next_id = 0

    def reset(self) -> None:
        """Clears all tracked zones (fresh backtest run)."""
        self.bull_obs.clear()
        self.bear_obs.clear()
        self._next_id = 0

    def _new_id(self, direction: PineOBDirection) -> str:
        self._next_id += 1
        return f"pineob_{direction.value.lower()}_{self._next_id}"

    def update(self, bars: list[Bar]) -> None:
        """Advances the tracker by one closed bar (bars[-1] is that bar).

        Order matches the Pine source's _handle_all: mitigation deletion of
        existing zones runs first using the just-closed bar's wicks, then new
        zone detection/eviction/dedup runs against the prior two bars.
        """
        if len(bars) < 2:
            return
        prev, curr = bars[-2], bars[-1]

        # "Normal" mitigation, wick-based (mitig_type is hardcoded "Wicks" in
        # the Pine source): a bull zone is deleted once price wicks below its
        # bottom; a bear zone once price wicks above its top.
        self.bull_obs = [ob for ob in self.bull_obs if not (curr.low < ob.bottom)]
        self.bear_obs = [ob for ob in self.bear_obs if not (curr.high > ob.top)]

        is_bull = prev.open > prev.close and curr.open < curr.close and curr.close > prev.high
        is_bear = prev.open < prev.close and curr.open > curr.close and curr.close < prev.low

        if is_bull:
            # Pine evicts the oldest zone whenever a new candidate is found,
            # even if that candidate later turns out to be a duplicate below.
            if len(self.bull_obs) > self.max_active:
                self.bull_obs.pop(0)
            top, bottom = prev.high, prev.open
            if not any(ob.top == top for ob in self.bull_obs):
                self.bull_obs.append(
                    PineOrderBlock(
                        id=self._new_id(PineOBDirection.BULLISH),
                        bar_index=len(bars) - 2,
                        top=top,
                        bottom=bottom,
                        direction=PineOBDirection.BULLISH,
                        timestamp=prev.timestamp,
                    )
                )

        if is_bear:
            if len(self.bear_obs) > self.max_active:
                self.bear_obs.pop(0)
            top, bottom = prev.open, prev.low
            if not any(ob.top == top for ob in self.bear_obs):
                self.bear_obs.append(
                    PineOrderBlock(
                        id=self._new_id(PineOBDirection.BEARISH),
                        bar_index=len(bars) - 2,
                        top=top,
                        bottom=bottom,
                        direction=PineOBDirection.BEARISH,
                        timestamp=prev.timestamp,
                    )
                )
