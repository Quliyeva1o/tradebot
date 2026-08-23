"""Chart overlay data aggregation for the price-chart dashboard.

Pure data-shaping layer: reads an already-built MarketState and reshapes it
into the flat structures a renderer needs, without importing matplotlib (or
any other plotting library) so this module stays reusable by a future
interactive renderer (Streamlit/Plotly) without duplicating the aggregation
logic -- see dashboard/static_renderer.py for the current (matplotlib)
consumer.
"""

from dataclasses import dataclass
from typing import Literal

from core.models import Bar
from market_structure.structure_models import (
    MarketState,
    StructureBreak,
    StructureTrend,
)
from market_structure.swing_models import Swing, SwingClassification, SwingType
from smc.fvg import FairValueGap
from smc.order_block import OrderBlock
from smc.premium_discount import PremiumDiscountZone


@dataclass(frozen=True)
class ChartOverlayData:
    """Everything a chart renderer needs to draw one symbol/timeframe's state."""

    bars: list[Bar]
    swings: list[Swing]
    structure_breaks: list[StructureBreak]
    order_blocks: list[OrderBlock]
    fair_value_gaps: list[FairValueGap]
    premium_discount_zone: PremiumDiscountZone | None
    trend: StructureTrend
    confidence: float


def build_overlay_data(market_state: MarketState) -> ChartOverlayData:
    """Reshapes a MarketState into ChartOverlayData. No new computation.

    Args:
        market_state: An already-populated MarketState (e.g. via
            MarketStateBuilder.initialize()/append_bar()).

    Returns:
        A ChartOverlayData snapshot of market_state's current fields.
    """
    return ChartOverlayData(
        bars=market_state.bars,
        swings=market_state.swing_graph.nodes,
        structure_breaks=list(market_state.structure_state.breaks_history),
        order_blocks=list(market_state.smc_state.order_blocks),
        fair_value_gaps=list(market_state.smc_state.fair_value_gaps),
        premium_discount_zone=market_state.premium_discount_zone,
        trend=market_state.structure_state.trend,
        confidence=market_state.structure_state.confidence,
    )


@dataclass(frozen=True)
class TrendLine:
    """A straight line drawn across two anchor points (bar_index, price)."""

    point_a: tuple[int, float]
    point_b: tuple[int, float]
    kind: Literal["support", "resistance"]


def compute_trend_lines(swings: list[Swing]) -> list[TrendLine]:
    """Connects the two most recent MAJOR swing lows/highs into trend lines.

    Args:
        swings: Chronologically ordered swings (e.g. MarketState.swing_graph.nodes).

    Returns:
        Up to two TrendLine objects (support from swing lows, resistance
        from swing highs). A given kind is omitted if fewer than 2 MAJOR
        swings of that type exist.
    """
    lows = [
        s for s in swings if s.type == SwingType.LOW and s.classification == SwingClassification.MAJOR
    ]
    highs = [
        s for s in swings if s.type == SwingType.HIGH and s.classification == SwingClassification.MAJOR
    ]

    lines: list[TrendLine] = []
    support = _fit_trend_line(lows[-2:], "support")
    if support is not None:
        lines.append(support)
    resistance = _fit_trend_line(highs[-2:], "resistance")
    if resistance is not None:
        lines.append(resistance)
    return lines


def _fit_trend_line(
    anchor_swings: list[Swing], kind: Literal["support", "resistance"]
) -> TrendLine | None:
    """Builds a TrendLine connecting the two given anchor swings directly."""
    if len(anchor_swings) < 2:
        return None

    first, last = anchor_swings[0], anchor_swings[-1]
    return TrendLine(
        point_a=(first.index, first.price), point_b=(last.index, last.price), kind=kind
    )
