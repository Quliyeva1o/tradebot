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

    assert graph.node_count() == 3
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

    # node_at must match indexing into the (copying) .nodes property, copy-free
    for i, expected in enumerate(graph.nodes):
        assert graph.node_at(i) is expected
    assert graph.node_at(-1) is None
    assert graph.node_at(3) is None


def test_market_state_root() -> None:
    """Verifies MarketState root aggregate lifecycle operations."""
    state = MarketState(symbol="GBPUSD", timeframe=Timeframe.M15)

    assert state.symbol == "GBPUSD"
    assert state.timeframe == Timeframe.M15
    assert len(state.bars) == 0
    assert state.bar_count() == 0
    assert state.get_latest_bar() is None

    # Given
    bar1 = Bar(datetime(2026, 7, 1, 12, 0), 1.2000, 1.2050, 1.1990, 1.2010, 500)
    bar2 = Bar(datetime(2026, 7, 1, 12, 15), 1.2010, 1.2080, 1.2000, 1.2060, 600)

    # When
    state.append_bar(bar1)
    state.append_bar(bar2)

    # Then
    assert len(state.bars) == 2
    assert state.bar_count() == 2
    assert state.bar_count() == len(state.bars)
    assert state.get_latest_bar() == bar2


def test_swing_graph_optimized_nodes_access() -> None:
    """Verifies optimized helper methods (recent_nodes, node_count, last_node)."""
    graph = SwingGraph()
    assert graph.node_count() == 0
    assert graph.last_node() is None
    assert graph.recent_nodes(5) == []

    s1 = Swing("swing_1", datetime(2026, 7, 1), 1, 1.10, SwingType.HIGH)
    s2 = Swing("swing_2", datetime(2026, 7, 1), 2, 1.09, SwingType.LOW)

    graph.add_swing(s1)
    assert graph.node_count() == 1
    assert graph.last_node() == s1
    assert graph.recent_nodes(5) == [s1]

    graph.add_swing(s2)
    assert graph.node_count() == 2
    assert graph.last_node() == s2
    assert graph.recent_nodes(1) == [s2]
    assert graph.recent_nodes(5) == [s1, s2]

    # Micro-benchmark
    import time
    # Create large graph (10,000 nodes)
    bench_graph = SwingGraph()
    for idx in range(10000):
        bench_graph.add_swing(Swing(f"s_{idx}", datetime(2026, 1, 1), idx, 1.0, SwingType.HIGH))

    iterations = 100000

    # Measure batch nodes[-5:]
    start_batch = time.perf_counter()
    for _ in range(iterations):
        _ = bench_graph.nodes[-5:]
    time_batch = time.perf_counter() - start_batch

    # Measure recent_nodes(5)
    start_inc = time.perf_counter()
    for _ in range(iterations):
        _ = bench_graph.recent_nodes(5)
    time_inc = time.perf_counter() - start_inc

    print(f"\n[Micro-benchmark Result] Graph size: 10,000 nodes, Iterations: {iterations}")
    print(f"Batch nodes[-5:] time: {time_batch:.4f} seconds")
    print(f"recent_nodes(5) time: {time_inc:.4f} seconds")
    print(f"Speedup: {time_batch / time_inc if time_inc > 0 else float('inf'):.2f}x")

