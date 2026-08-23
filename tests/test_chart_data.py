"""Unit tests for dashboard/chart_data.py."""

from datetime import datetime, timedelta

from core.models import Bar, Timeframe
from dashboard.chart_data import build_overlay_data, compute_trend_lines
from market_structure.structure_models import (
    BreakType,
    MarketState,
    SMCState,
    StructureBreak,
    StructureState,
    StructureTrend,
    SwingGraph,
)
from market_structure.swing_models import Swing, SwingClassification, SwingType
from smc.fvg import FairValueGap, FVGDirection
from smc.order_block import OBDirection, OrderBlock
from smc.premium_discount import PremiumDiscountZone, ZoneType

_START = datetime(2026, 1, 1)


def _bar(index: int, price: float) -> Bar:
    return Bar(
        timestamp=_START + timedelta(minutes=5 * index),
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=100.0,
    )


def _swing(index: int, price: float, swing_type: SwingType, major: bool = True) -> Swing:
    return Swing(
        id=f"swing_{index}_{swing_type.value}",
        timestamp=_START + timedelta(minutes=5 * index),
        index=index,
        price=price,
        type=swing_type,
        classification=SwingClassification.MAJOR if major else SwingClassification.MINOR,
    )


class TestBuildOverlayData:
    def test_passes_through_market_state_fields(self) -> None:
        bars = [_bar(0, 100.0), _bar(1, 101.0)]
        swing = _swing(0, 100.0, SwingType.LOW)
        ob = OrderBlock(
            id="ob_0_bullish",
            bar_index=0,
            high=101.0,
            low=99.0,
            direction=OBDirection.BULLISH,
            timestamp=_START,
        )
        fvg = FairValueGap(
            id="fvg_0_bullish",
            start_index=0,
            end_index=2,
            upper_price=101.0,
            lower_price=100.0,
            direction=FVGDirection.BULLISH,
            timestamp=_START,
        )
        brk = StructureBreak(
            break_id="brk_1",
            break_type=BreakType.BOS,
            broken_swing=swing,
            breaking_bar=bars[-1],
            timestamp=_START,
        )
        zone = PremiumDiscountZone(high=105.0, low=95.0, equilibrium=100.0, current_price=101.0, zone=ZoneType.PREMIUM)

        swing_graph = SwingGraph()
        swing_graph.add_swing(swing)
        market_state = MarketState(
            symbol="EURUSD",
            timeframe=Timeframe.M5,
            _bars=bars,
            swing_graph=swing_graph,
            structure_state=StructureState(
                trend=StructureTrend.BULLISH, confidence=0.8, breaks_history=[brk]
            ),
            smc_state=SMCState(fair_value_gaps=[fvg], order_blocks=[ob]),
            premium_discount_zone=zone,
        )

        overlay = build_overlay_data(market_state)

        assert overlay.bars == bars
        assert overlay.swings == [swing]
        assert overlay.structure_breaks == [brk]
        assert overlay.order_blocks == [ob]
        assert overlay.fair_value_gaps == [fvg]
        assert overlay.premium_discount_zone is zone
        assert overlay.trend == StructureTrend.BULLISH
        assert overlay.confidence == 0.8


class TestComputeTrendLines:
    def test_returns_empty_with_fewer_than_two_swings_of_a_type(self) -> None:
        swings = [_swing(0, 100.0, SwingType.LOW)]
        assert compute_trend_lines(swings) == []

    def test_connects_two_most_recent_major_lows_and_highs(self) -> None:
        swings = [
            _swing(0, 100.0, SwingType.LOW),
            _swing(5, 110.0, SwingType.HIGH),
            _swing(10, 102.0, SwingType.LOW),
            _swing(15, 112.0, SwingType.HIGH),
        ]

        lines = compute_trend_lines(swings)

        kinds = {line.kind for line in lines}
        assert kinds == {"support", "resistance"}
        support = next(line for line in lines if line.kind == "support")
        assert support.point_a == (0, 100.0)
        assert support.point_b == (10, 102.0)
        resistance = next(line for line in lines if line.kind == "resistance")
        assert resistance.point_a == (5, 110.0)
        assert resistance.point_b == (15, 112.0)

    def test_ignores_minor_swings(self) -> None:
        swings = [
            _swing(0, 100.0, SwingType.LOW, major=False),
            _swing(10, 102.0, SwingType.LOW, major=False),
        ]
        assert compute_trend_lines(swings) == []
