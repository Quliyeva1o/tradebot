"""Unit tests for the Core Domain Modeling Foundation."""

from datetime import datetime

from core.models import Bar, ConfluenceMetadata, SignalDirection, Timeframe, TradeSignal
from market_structure.structure_models import (
    MarketState,
    SwingGraph,
)
from market_structure.swing_models import Swing, SwingType


def test_trade_signal_model() -> None:
    """Verifies TradeSignal attributes and constructor."""
    meta = ConfluenceMetadata(
        setup_type="OrderBlockMitigation", invalidation_type="Structural", risk_reward_ratio=3.0
    )
    signal = TradeSignal(
        signal_id="sig_1",
        symbol="EURUSD",
        timeframe=Timeframe.H1,
        direction=SignalDirection.BUY,
        entry_price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1150,
        confluence=meta,
    )

    assert signal.signal_id == "sig_1"
    assert signal.direction == SignalDirection.BUY
    assert signal.entry_price == 1.1000
    assert signal.confluence.setup_type == "OrderBlockMitigation"
    assert signal.confluence.risk_reward_ratio == 3.0


def test_swing_graph_nodes_and_queries() -> None:
    """Verifies SwingGraph node insertion, latest query, and equal high/low logic."""
    graph = SwingGraph()

    # Create dummy wicks
    s1 = Swing(
        id="swing_1",
        timestamp=datetime(2026, 7, 1, 12, 0),
        index=4,
        price=1.1050,
        type=SwingType.HIGH,
    )
    s2 = Swing(
        id="swing_2",
        timestamp=datetime(2026, 7, 1, 13, 0),
        index=8,
        price=1.0950,
        type=SwingType.LOW,
        previous_id="swing_1",
    )
    s3 = Swing(
        id="swing_3",
        timestamp=datetime(2026, 7, 1, 14, 0),
        index=12,
        price=1.1050,  # Equal High with swing_1
        type=SwingType.HIGH,
        previous_id="swing_2",
    )

    graph.add_swing(s1)
    graph.add_swing(s2)
    graph.add_swing(s3)

    assert len(graph.nodes) == 3
    assert graph.edges["swing_1"] == ["swing_2"]
    assert graph.edges["swing_2"] == ["swing_3"]

    latest_high = graph.get_latest_high()
    latest_low = graph.get_latest_low()
    assert latest_high is not None
    assert latest_low is not None
    assert latest_high.id == "swing_3"
    assert latest_low.id == "swing_2"

    # Verify equal highs grouped (swing_1 and swing_3 have price 1.1050)
    eq_highs = graph.find_equal_highs(tolerance=0.0001)
    assert len(eq_highs) == 2
    assert "swing_1" in [s.id for s in eq_highs]
    assert "swing_3" in [s.id for s in eq_highs]


def test_market_state_root() -> None:
    """Verifies MarketState root aggregate lifecycle operations."""
    state = MarketState(symbol="GBPUSD", timeframe=Timeframe.M15)

    assert state.symbol == "GBPUSD"
    assert state.timeframe == Timeframe.M15
    assert len(state.bars) == 0
    assert state.get_latest_bar() is None

    # Given
    bar1 = Bar(datetime(2026, 7, 1, 12, 0), 1.2000, 1.2050, 1.1990, 1.2010, 500)
    bar2 = Bar(datetime(2026, 7, 1, 12, 15), 1.2010, 1.2080, 1.2000, 1.2060, 600)

    # When
    state.append_bar(bar1)
    state.append_bar(bar2)

    # Then
    assert len(state.bars) == 2
    assert state.get_latest_bar() == bar2
