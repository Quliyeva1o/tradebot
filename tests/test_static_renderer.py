"""Unit tests for dashboard/static_renderer.py."""

from datetime import datetime, timedelta

from core.models import Bar, Timeframe
from dashboard.chart_data import ChartOverlayData, TrendLine
from dashboard.static_renderer import render_price_chart
from market_structure.structure_models import StructureTrend
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


def _overlay_data(bars: list[Bar]) -> ChartOverlayData:
    return ChartOverlayData(
        bars=bars,
        swings=[],
        structure_breaks=[],
        order_blocks=[],
        fair_value_gaps=[],
        premium_discount_zone=None,
        trend=StructureTrend.UNKNOWN,
        confidence=0.0,
    )


class TestRenderPriceChart:
    def test_writes_a_nonempty_png(self, tmp_path) -> None:
        bars = [_bar(i, 100.0 + i) for i in range(20)]
        output_path = tmp_path / "chart.png"

        result = render_price_chart(_overlay_data(bars), [], output_path)

        assert result == output_path
        assert output_path.exists()
        assert output_path.stat().st_size > 0

    def test_does_not_raise_with_empty_overlays(self, tmp_path) -> None:
        output_path = tmp_path / "empty.png"
        render_price_chart(_overlay_data([]), [], output_path)
        assert output_path.exists()
        assert output_path.stat().st_size > 0

    def test_renders_with_all_overlay_kinds_present(self, tmp_path) -> None:
        from market_structure.structure_models import BreakType, StructureBreak
        from market_structure.swing_models import Swing, SwingClassification, SwingType
        from smc.fvg import FairValueGap, FVGDirection
        from smc.order_block import OBDirection, OrderBlock

        bars = [_bar(i, 100.0 + i) for i in range(10)]
        swing = Swing(
            id="swing_2_low",
            timestamp=_START,
            index=2,
            price=99.0,
            type=SwingType.LOW,
            classification=SwingClassification.MAJOR,
        )
        ob = OrderBlock(
            id="ob_1_bullish",
            bar_index=1,
            high=102.0,
            low=100.0,
            direction=OBDirection.BULLISH,
            timestamp=_START,
            is_mitigated=True,
        )
        fvg = FairValueGap(
            id="fvg_0_bearish",
            start_index=0,
            end_index=2,
            upper_price=103.0,
            lower_price=101.0,
            direction=FVGDirection.BEARISH,
            timestamp=_START,
        )
        brk = StructureBreak(
            break_id="brk_1",
            break_type=BreakType.CHoCH,
            broken_swing=swing,
            breaking_bar=bars[-1],
            timestamp=_START,
        )
        zone = PremiumDiscountZone(high=110.0, low=100.0, equilibrium=105.0, current_price=104.0, zone=ZoneType.DISCOUNT)
        data = ChartOverlayData(
            bars=bars,
            swings=[swing],
            structure_breaks=[brk],
            order_blocks=[ob],
            fair_value_gaps=[fvg],
            premium_discount_zone=zone,
            trend=StructureTrend.BEARISH,
            confidence=0.5,
        )
        trend_lines = [TrendLine(point_a=(0, 99.0), point_b=(5, 101.0), kind="support")]

        output_path = tmp_path / "full.png"
        render_price_chart(data, trend_lines, output_path, title="test chart")

        assert output_path.stat().st_size > 0
