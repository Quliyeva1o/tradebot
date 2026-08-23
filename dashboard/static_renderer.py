"""Static (matplotlib PNG) price-chart renderer with SMC overlays.

Draws candles manually via Rectangle/Line2D (no mplfinance dependency --
matplotlib is already a core project dependency, mplfinance is not) plus
order-block/FVG/structure-break/premium-discount/trend-line overlays sourced
from dashboard.chart_data.ChartOverlayData. Same Agg-backend, savefig-to-PNG
convention as run_backtest.py's equity-curve export and
research/stability.py's heatmap.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

from core.models import Bar
from dashboard.chart_data import ChartOverlayData, TrendLine
from market_structure.structure_models import BreakType, StructureBreak
from market_structure.swing_models import Swing, SwingType
from smc.fvg import FairValueGap, FVGDirection
from smc.order_block import OBDirection, OrderBlock
from smc.premium_discount import PremiumDiscountZone

# Chart color system (dataviz skill palette.md): bullish/bearish body colors,
# categorical slots for OB/FVG chosen to avoid adjacent-hue confusion (skips
# yellow, which would sit next to orange for bearish OBs), muted ink for
# structure lines (never color-only -- always paired with a text label).
_BULLISH_COLOR = "#0ca30c"
_BEARISH_COLOR = "#d03b3b"
_OB_BULLISH_COLOR = "#2a78d6"
_OB_BEARISH_COLOR = "#eb6834"
_FVG_BULLISH_COLOR = "#1baf7a"
_FVG_BEARISH_COLOR = "#4a3aa7"
_LINE_COLOR = "#898781"
_SURFACE_COLOR = "#fcfcfb"
_GRID_COLOR = "#e1e0d9"

_CANDLE_BODY_HALF_WIDTH = 0.3


def render_price_chart(
    data: ChartOverlayData,
    trend_lines: list[TrendLine],
    output_path: Path,
    title: str = "",
) -> Path:
    """Renders a candlestick chart with SMC/structure overlays to a PNG file.

    Args:
        data: The overlay data to draw (see dashboard.chart_data.build_overlay_data).
        trend_lines: Support/resistance trend lines (see
            dashboard.chart_data.compute_trend_lines).
        output_path: Destination PNG path. Parent directories are created if missing.
        title: Chart title. Defaults to a summary of bar count/trend/confidence.

    Returns:
        output_path, for convenient chaining by the caller.
    """
    bars = data.bars
    bar_count = len(bars)

    fig, ax = plt.subplots(figsize=(16, 9))
    fig.patch.set_facecolor(_SURFACE_COLOR)
    ax.set_facecolor(_SURFACE_COLOR)
    ax.grid(True, color=_GRID_COLOR, linewidth=0.5, zorder=0)

    _draw_candles(ax, bars)
    _draw_order_blocks(ax, data.order_blocks, bar_count)
    _draw_fair_value_gaps(ax, data.fair_value_gaps, bar_count)
    _draw_premium_discount(ax, data.premium_discount_zone)
    _draw_structure_breaks(ax, data.structure_breaks, bar_count)
    _draw_trend_lines(ax, trend_lines)
    _draw_swings(ax, data.swings)

    ax.set_xlim(-1, max(bar_count, 1))
    ax.set_xlabel("Bar index")
    ax.set_ylabel("Price")
    ax.set_title(title or f"{bar_count} bars | trend={data.trend.value} confidence={data.confidence:.2f}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return output_path


def _draw_candles(ax: Axes, bars: list[Bar]) -> None:
    for idx, bar in enumerate(bars):
        color = _BULLISH_COLOR if bar.close >= bar.open else _BEARISH_COLOR
        ax.add_line(Line2D([idx, idx], [bar.low, bar.high], color=color, linewidth=1.0, zorder=3))
        body_low = min(bar.open, bar.close)
        body_height = max(abs(bar.close - bar.open), 1e-9)
        ax.add_patch(
            Rectangle(
                (idx - _CANDLE_BODY_HALF_WIDTH, body_low),
                2 * _CANDLE_BODY_HALF_WIDTH,
                body_height,
                facecolor=color,
                edgecolor=color,
                zorder=4,
            )
        )


def _draw_order_blocks(ax: Axes, order_blocks: list[OrderBlock], bar_count: int) -> None:
    for ob in order_blocks:
        color = _OB_BULLISH_COLOR if ob.direction == OBDirection.BULLISH else _OB_BEARISH_COLOR
        alpha = 0.06 if ob.is_mitigated else 0.16
        linestyle = "dashed" if ob.is_mitigated else "solid"
        width = max(bar_count - ob.bar_index, 1)
        ax.add_patch(
            Rectangle(
                (ob.bar_index, ob.low),
                width,
                ob.high - ob.low,
                facecolor=color,
                edgecolor=color,
                alpha=alpha,
                linestyle=linestyle,
                linewidth=0.8,
                zorder=1,
            )
        )


def _draw_fair_value_gaps(ax: Axes, fvgs: list[FairValueGap], bar_count: int) -> None:
    for fvg in fvgs:
        color = _FVG_BULLISH_COLOR if fvg.direction == FVGDirection.BULLISH else _FVG_BEARISH_COLOR
        alpha = 0.06 if fvg.is_mitigated else 0.16
        linestyle = "dashed" if fvg.is_mitigated else "solid"
        width = max(bar_count - fvg.start_index, 1)
        ax.add_patch(
            Rectangle(
                (fvg.start_index, fvg.lower_price),
                width,
                fvg.upper_price - fvg.lower_price,
                facecolor=color,
                edgecolor=color,
                alpha=alpha,
                linestyle=linestyle,
                linewidth=0.8,
                zorder=1,
            )
        )


def _draw_premium_discount(ax: Axes, zone: PremiumDiscountZone | None) -> None:
    if zone is None:
        return
    ax.axhspan(zone.equilibrium, zone.high, color=_BEARISH_COLOR, alpha=0.04, zorder=0)
    ax.axhspan(zone.low, zone.equilibrium, color=_BULLISH_COLOR, alpha=0.04, zorder=0)
    ax.axhline(zone.equilibrium, color=_LINE_COLOR, linestyle="dashed", linewidth=1.0, zorder=2)


def _draw_structure_breaks(ax: Axes, breaks: list[StructureBreak], bar_count: int) -> None:
    x_end = max(bar_count - 1, 0)
    for brk in breaks:
        x0 = brk.broken_swing.index
        y = brk.broken_swing.price
        is_choch = brk.break_type == BreakType.CHoCH
        ax.hlines(
            y,
            x0,
            max(x_end, x0),
            colors=_LINE_COLOR,
            linestyles="dashed",
            linewidth=2.0 if is_choch else 1.5,
            zorder=2,
        )
        ax.annotate(
            "CHoCH" if is_choch else "BOS",
            xy=(x_end, y),
            xytext=(3, 0),
            textcoords="offset points",
            fontsize=8,
            fontweight="bold" if is_choch else "normal",
            color=_LINE_COLOR,
        )


def _draw_trend_lines(ax: Axes, trend_lines: list[TrendLine]) -> None:
    for line in trend_lines:
        (x0, y0), (x1, y1) = line.point_a, line.point_b
        ax.plot([x0, x1], [y0, y1], color=_LINE_COLOR, linestyle="dashed", linewidth=1.5, zorder=2)
        ax.annotate(
            line.kind,
            xy=(x1, y1),
            xytext=(3, 0),
            textcoords="offset points",
            fontsize=8,
            color=_LINE_COLOR,
        )


def _draw_swings(ax: Axes, swings: list[Swing]) -> None:
    for swing in swings:
        marker = "v" if swing.type == SwingType.HIGH else "^"
        ax.scatter(swing.index, swing.price, marker=marker, color=_LINE_COLOR, s=20, zorder=5)
