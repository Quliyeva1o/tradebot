"""Tests for the production-grade BacktestEngine."""

from datetime import datetime, timedelta

import pytest

from application.services.market_state_builder import MarketStateBuilder
from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig, TradeResult
from core.models import Bar, SignalDirection, Timeframe
from market_structure.structure_models import MarketState
from strategy.models import TradeSetup


def _create_bar(index: int, open_p: float, high_p: float, low_p: float, close_p: float) -> Bar:
    """Helper to generate a Bar object."""
    return Bar(
        timestamp=datetime(2026, 1, 1) + timedelta(minutes=15 * index),
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=100.0,
    )


class MockEvaluator:
    """Mock strategy evaluator returning pre-configured setups per bar iteration."""

    def __init__(self, setups: list[list[TradeSetup]]) -> None:
        self.setups = setups
        self.call_count = 0

    def run(self, market_state: MarketState) -> list[TradeSetup]:
        if self.call_count < len(self.setups):
            res = self.setups[self.call_count]
        else:
            res = []
        self.call_count += 1
        return res

    def reset(self) -> None:
        pass



@pytest.fixture
def base_config() -> BacktestConfig:
    """Fixture returning base backtester config."""
    return BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.01,  # 1% risk
        spread=0.0,
        commission=5.0,  # $5 commission flat per trade
        slippage=0.0,
        max_holding_bars=None,
    )


@pytest.fixture
def state_builder() -> MarketStateBuilder:
    """Fixture returning state builder instance."""
    return MarketStateBuilder(symbol="EURUSD", timeframe=Timeframe.M15)


def test_backtest_winning_trade(
    base_config: BacktestConfig, state_builder: MarketStateBuilder
) -> None:
    """Verifies a successful winning BUY trade completes with positive PnL."""
    setup = TradeSetup(
        setup_id="setup_win",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),  # SL price is 1.0980
        target_zone=(1.1050, 1.1060),  # TP price is 1.1050
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )

    evaluator = MockEvaluator([[setup], [], []])
    engine = BacktestEngine(config=base_config)

    # Candles
    # Candle 0: triggers signal
    # Candle 1: MARKET order fills unconditionally at this bar's own open (1.1020)
    # Candle 2: High hits TP (1.1060)
    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1060, 1.1010, 1.1055),
    ]

    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.result == TradeResult.WIN
    assert trade.entry_price == 1.1020
    assert trade.exit_price == 1.1050
    assert trade.pnl > 0.0
    assert result.total_profit > 0.0
    assert result.win_rate == 1.0
    assert result.profit_factor == float("inf")


def test_backtest_losing_trade(
    base_config: BacktestConfig, state_builder: MarketStateBuilder
) -> None:
    """Verifies a losing trade triggers stop loss and records negative PnL."""
    setup = TradeSetup(
        setup_id="setup_loss",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),  # SL price is 1.0980
        target_zone=(1.1050, 1.1060),
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )

    evaluator = MockEvaluator([[setup], [], []])
    engine = BacktestEngine(config=base_config)

    # Candles
    # Candle 0: triggers signal
    # Candle 1: MARKET order fills unconditionally at this bar's own open (1.1020)
    # Candle 2: Low hits SL (1.0970 <= 1.0980)
    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1020, 1.0970, 1.0985),
    ]

    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.result == TradeResult.LOSS
    assert trade.exit_price == 1.0980
    assert trade.pnl < 0.0
    assert result.total_profit < 0.0
    assert result.win_rate == 0.0
    assert result.profit_factor == 0.0


def test_backtest_no_signals(
    base_config: BacktestConfig, state_builder: MarketStateBuilder
) -> None:
    """Verifies that backtest finishes safely with empty trades list when no signals occur."""
    evaluator = MockEvaluator([[], []])
    engine = BacktestEngine(config=base_config)

    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
    ]

    result = engine.run(candles, evaluator, state_builder)
    assert len(result.trades) == 0
    assert result.total_profit == 0.0
    assert result.win_rate == 0.0
    assert result.profit_factor == 1.0


def test_backtest_same_candle_sl_and_tp(
    base_config: BacktestConfig, state_builder: MarketStateBuilder
) -> None:
    """Verifies that if both SL and TP are hit on the same candle, SL is assumed first (LOSS)."""
    setup = TradeSetup(
        setup_id="setup_conflict",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),  # SL = 1.0980
        target_zone=(1.1050, 1.1060),  # TP = 1.1050
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )

    evaluator = MockEvaluator([[setup], [], []])
    engine = BacktestEngine(config=base_config)

    # Candles
    # Candle 0: triggers signal
    # Candle 1: Low triggers entry
    # Candle 2: High is 1.1060 (hits TP) and Low is 1.0970 (hits SL)
    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1060, 1.0970, 1.1020),
    ]

    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.result == TradeResult.LOSS
    assert trade.exit_price == 1.0980


def test_backtest_drawdown_calculation(state_builder: MarketStateBuilder) -> None:
    """Verifies drawdown calculation matches peak-to-trough balance changes."""
    setup1 = TradeSetup(
        setup_id="setup_1",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),
        target_zone=(1.1050, 1.1060),
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )
    setup2 = TradeSetup(
        setup_id="setup_2",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),
        target_zone=(1.1050, 1.1060),
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )

    evaluator = MockEvaluator([[setup1], [], [], [], [setup2], [], [], []])

    # Commission is 0.0 to make drawdown math exact
    config = BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.10,  # 10% risk
        spread=0.0,
        commission=0.0,
        slippage=0.0,
        max_holding_bars=None,
    )
    engine = BacktestEngine(config=config)

    # Candles. Entry-fill bars' OPEN is set to entry_zone's low edge (1.0990,
    # the BUY-direction sizing price resolve_entry_price() resolves to) so
    # the position-sizing reference price and the actual MARKET-fill price
    # coincide -- a MARKET order fills at the entry-fill bar's own open
    # unconditionally now (see BacktestEngine.run()'s step 3), not at any
    # zone edge, so this is just a convenient choice, not a requirement.
    # Trade 1: entries at 1.0990, exits winning at 1.1050 (+$6000.00)
    # Trade 2: entries at 1.0990, exits losing at 1.0980 (-$1600.00)
    candles = [
        # T1 setup (Candle 0) - Call 0
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        # T1 entry (Candle 1)
        _create_bar(1, 1.0990, 1.1030, 1.1000, 1.1015),
        # T1 TP (Candle 2) - Call 1
        _create_bar(2, 1.1015, 1.1060, 1.1010, 1.1055),
        # Candle 3 - Call 2
        _create_bar(3, 1.1020, 1.1030, 1.1015, 1.1020),
        # T2 setup (Candle 4) - Call 3
        _create_bar(4, 1.1020, 1.1030, 1.1015, 1.1020),
        # T2 setup trigger (Candle 5) - Call 4
        _create_bar(5, 1.1020, 1.1030, 1.1015, 1.1020),
        # T2 entry (Candle 6)
        _create_bar(6, 1.0990, 1.1030, 1.1000, 1.1015),
        # T2 SL (Candle 7)
        _create_bar(7, 1.1015, 1.1020, 1.0970, 1.0985),
    ]

    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 2
    assert result.trades[0].result == TradeResult.WIN
    assert result.trades[1].result == TradeResult.LOSS

    # Balance peak is initial_balance + trade 1 profit: $10000.0 + $6000.00 = $16000.00
    # Balance trough is $16000.00 - trade 2 loss: $16000.00 - $1600.00 = $14400.00
    # Expected relative drawdown: (16000 - 14400) / 16000 = 1600 / 16000 = 10.0%
    assert 0.099 < result.max_drawdown < 0.101


def test_exit_spread_slippage_symmetry(state_builder: MarketStateBuilder) -> None:
    """Verifies that spread and slippage are correctly and symmetrically applied to exit prices (TP/SL/Expired)."""
    # 1. BUY trade SL and TP exits
    config = BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.01,
        spread=0.0002,
        commission=0.0,
        slippage=0.0001,
        max_holding_bars=None,
    )

    setup_win = TradeSetup(
        setup_id="setup_win",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),  # SL 1.0980
        target_zone=(1.1050, 1.1060),  # TP 1.1050
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )

    # TP hit. Entry-fill bar's open (candle 1) is set to entry_zone's low
    # edge (1.0990, the BUY sizing price resolve_entry_price() resolves to)
    # so it's easy to hand-verify -- a MARKET order fills at this bar's own
    # open unconditionally now, not at any zone edge.
    candles_tp = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.0990, 1.1030, 1.1000, 1.1015), # Entry at 1.0990 + 0.0001 + 0.0001 = 1.0992
        _create_bar(2, 1.1015, 1.1060, 1.1010, 1.1055), # TP hit at 1.1050
    ]

    evaluator = MockEvaluator([[setup_win], [], []])
    engine = BacktestEngine(config=config)
    result = engine.run(candles_tp, evaluator, state_builder)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.result == TradeResult.WIN
    assert trade.entry_price == pytest.approx(1.0992)
    # BUY TP exit: exit_price = tp - spread/2 - slippage = 1.1050 - 0.0001 - 0.0001 = 1.1048
    assert trade.exit_price == pytest.approx(1.1048)

    # SL hit
    candles_sl = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.0990, 1.1030, 1.1000, 1.1015), # Entry at 1.0992
        _create_bar(2, 1.1015, 1.1020, 1.0970, 1.0985), # SL hit at 1.0980
    ]
    evaluator = MockEvaluator([[setup_win], [], []])
    result = engine.run(candles_sl, evaluator, state_builder)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.result == TradeResult.LOSS
    # BUY SL exit: exit_price = sl - spread/2 - slippage = 1.0980 - 0.0001 - 0.0001 = 1.0978
    assert trade.exit_price == pytest.approx(1.0978)

    # 2. SELL trade SL and TP exits
    setup_sell = TradeSetup(
        setup_id="setup_sell",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.SELL,
        entry_zone=(1.0990, 1.1010),  # entry_low is 1.0990
        stop_zone=(1.1020, 1.1030),  # SL 1.1030
        target_zone=(1.0950, 1.0960),  # TP 1.0950
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bearish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )

    # SELL TP hit. Entry-fill bar's open (candle 1) is set to entry_zone's
    # high edge (1.1010, the SELL sizing price resolve_entry_price()
    # resolves to).
    candles_sell_tp = [
        _create_bar(0, 1.0980, 1.0985, 1.0970, 1.0980),
        _create_bar(1, 1.1010, 1.1010, 1.0970, 1.0980), # Entry at 1.1010 - 0.0001 - 0.0001 = 1.1008
        _create_bar(2, 1.0980, 1.0990, 1.0940, 1.0955), # TP hit at 1.0950
    ]
    evaluator = MockEvaluator([[setup_sell], [], []])
    result = engine.run(candles_sell_tp, evaluator, state_builder)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.result == TradeResult.WIN
    assert trade.entry_price == pytest.approx(1.1008)
    # SELL TP exit: exit_price = tp + spread/2 + slippage = 1.0960 + 0.0001 + 0.0001 = 1.0962
    assert trade.exit_price == pytest.approx(1.0962)


    # expired exit for BUY
    config_expired = BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.01,
        spread=0.0002,
        commission=0.0,
        slippage=0.0001,
        max_holding_bars=2,
    )
    candles_expired = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.0990, 1.1030, 1.1000, 1.1015), # Entry at 1.0992
        _create_bar(2, 1.1015, 1.1025, 1.1010, 1.1020), # bars_held = 1
        _create_bar(3, 1.1015, 1.1025, 1.1010, 1.1020), # bars_held = 2, close 1.1020
    ]
    evaluator = MockEvaluator([[setup_win], [], [], []])
    engine = BacktestEngine(config=config_expired)
    result = engine.run(candles_expired, evaluator, state_builder)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.result == TradeResult.EXPIRED
    # BUY expired: close - spread/2 - slippage = 1.1020 - 0.0001 - 0.0001 = 1.1018
    assert trade.exit_price == pytest.approx(1.1018)


def test_max_holding_bars_forces_close_after_n_bars(state_builder: MarketStateBuilder) -> None:
    """Verifies an open position is force-closed as EXPIRED once bars_held reaches max_holding_bars."""
    config = BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.01,
        spread=0.0,
        commission=0.0,
        slippage=0.0,
        max_holding_bars=3,
    )

    # SL/TP are set far outside the price range below, so neither is ever hit --
    # the only way this trade can close is via the max_holding_bars expiry.
    setup = TradeSetup(
        setup_id="setup_expiry",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0500, 1.0510),  # far below any candle low
        target_zone=(1.2000, 1.2010),  # far above any candle high
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )

    # Candle 0: triggers signal (pending setup proposed)
    # Candle 1: MARKET fill at this bar's own open (set to entry_zone's low
    #   edge, 1.0990, so it's easy to hand-verify -- bars_held starts at 0)
    # Candle 2: bars_held becomes 1, still open
    # Candle 3: bars_held becomes 2, still open
    # Candle 4: bars_held becomes 3 == max_holding_bars -> forced EXPIRED close at this candle's close
    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.0990, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1025, 1.1010, 1.1018),
        _create_bar(3, 1.1018, 1.1028, 1.1012, 1.1022),
        _create_bar(4, 1.1022, 1.1032, 1.1016, 1.1026),
    ]

    evaluator = MockEvaluator([[setup], [], [], [], []])
    engine = BacktestEngine(config=config)
    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.result == TradeResult.EXPIRED
    assert trade.bars_held == 3
    assert trade.entry_price == 1.0990
    assert trade.exit_price == pytest.approx(1.1026)  # candle 4's close, no spread/slippage configured
    assert trade.exit_time == candles[4].timestamp


def test_conditional_tp_extension_default_none_is_a_no_op(
    base_config: BacktestConfig, state_builder: MarketStateBuilder
) -> None:
    """Differential/regression test: TradeSetup's new conditional_tp_extension_*
    fields default to None, and every pre-existing strategy constructs
    TradeSetup without them. This must produce byte-identical results to the
    pre-change engine -- re-runs test_backtest_winning_trade's exact scenario
    and asserts the exact same outcome, proving the new opt-in branch in
    BacktestEngine.run() is a true no-op when unset.
    """
    setup = TradeSetup(
        setup_id="setup_win",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),
        target_zone=(1.1050, 1.1060),
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
        # conditional_tp_extension_bars/price intentionally omitted (default None)
    )

    evaluator = MockEvaluator([[setup], [], []])
    engine = BacktestEngine(config=base_config)

    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1060, 1.1010, 1.1055),
    ]

    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.result == TradeResult.WIN
    assert trade.entry_price == 1.1020
    assert trade.exit_price == 1.1050
    assert trade.pnl > 0.0
    assert result.total_profit > 0.0
    assert result.win_rate == 1.0
    assert result.profit_factor == float("inf")


def test_conditional_tp_extension_extends_when_hit_within_window(
    state_builder: MarketStateBuilder,
) -> None:
    """Verifies the opt-in conditional TP extension mechanism itself.

    TP1 (1.1050) is touched on bar 2, within the 3-bar extension window --
    the trade must NOT close there; instead take_profit extends to TP2
    (1.1100), and the trade only closes once TP2 is later touched.
    """
    config = BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.01,
        spread=0.0,
        commission=0.0,
        slippage=0.0,
        max_holding_bars=None,
    )
    setup = TradeSetup(
        setup_id="setup_extend",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),
        target_zone=(1.1050, 1.1050),  # TP1 = 1.1050
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
        conditional_tp_extension_bars=3,
        conditional_tp_extension_price=1.1100,  # TP2
    )

    # Candle 0: triggers signal
    # Candle 1: MARKET fill at this bar's own open, 1.1020 (bars_held=0 at fill)
    # Candle 2: bars_held becomes 1 (<=3 window), high touches TP1 (1.1050) -> extend, no close
    # Candle 3: bars_held becomes 2, price pulls back, no exit
    # Candle 4: bars_held becomes 3, high touches TP2 (1.1100) -> WIN at TP2
    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1055, 1.1010, 1.1040),
        _create_bar(3, 1.1040, 1.1045, 1.1020, 1.1030),
        _create_bar(4, 1.1030, 1.1110, 1.1025, 1.1090),
    ]

    evaluator = MockEvaluator([[setup], [], [], [], []])
    engine = BacktestEngine(config=config)
    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.result == TradeResult.WIN
    assert trade.entry_price == 1.1020
    assert trade.exit_price == pytest.approx(1.1100)  # TP2, not TP1
    assert trade.exit_time == candles[4].timestamp


def test_conditional_tp_extension_stays_at_tp1_outside_window(
    state_builder: MarketStateBuilder,
) -> None:
    """Verifies TP stays at TP1 (no extension) when TP1 is touched AFTER the
    extension window has elapsed -- the original 1R target is honored as-is,
    matching the spec's "else: no change" branch.
    """
    config = BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.01,
        spread=0.0,
        commission=0.0,
        slippage=0.0,
        max_holding_bars=None,
    )
    setup = TradeSetup(
        setup_id="setup_no_extend",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),
        target_zone=(1.1050, 1.1050),  # TP1 = 1.1050
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
        conditional_tp_extension_bars=3,
        conditional_tp_extension_price=1.1100,
    )

    # Candle 1: entry fills, bars_held becomes 1
    # Candles 2-4: price stays below TP1, bars_held reaches 4 (window of 3 elapsed)
    # Candle 5: high touches TP1 (1.1050) -> normal WIN at TP1 (no extension, window passed)
    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1020, 1.1005, 1.1015),
        _create_bar(3, 1.1015, 1.1020, 1.1005, 1.1015),
        _create_bar(4, 1.1015, 1.1020, 1.1005, 1.1015),
        _create_bar(5, 1.1015, 1.1060, 1.1010, 1.1050),
    ]

    evaluator = MockEvaluator([[setup], [], [], [], [], []])
    engine = BacktestEngine(config=config)
    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.result == TradeResult.WIN
    assert trade.entry_price == 1.1020
    assert trade.exit_price == pytest.approx(1.1050)  # TP1, extension window had passed
    assert trade.exit_time == candles[5].timestamp


def test_bar_spread_override(state_builder: MarketStateBuilder) -> None:
    """Verifies that Bar.spread > 0 overrides BacktestConfig.spread."""
    config = BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.01,
        spread=0.0001,
        commission=0.0,
        slippage=0.0001,
        max_holding_bars=None,
    )

    setup = TradeSetup(
        setup_id="setup",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),
        target_zone=(1.1050, 1.1060),
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )

    # Bar 1 has dynamic spread 0.0005. Bar 2 has dynamic spread 0.0004.
    candles = [
        Bar(timestamp=datetime(2026, 1, 1), open=1.1020, high=1.1030, low=1.1015, close=1.1020, volume=100.0, spread=0.0),
        Bar(timestamp=datetime(2026, 1, 1, 0, 15), open=1.1020, high=1.1030, low=1.1000, close=1.1015, volume=100.0, spread=0.0005),
        Bar(timestamp=datetime(2026, 1, 1, 0, 30), open=1.1015, high=1.1060, low=1.1010, close=1.1055, volume=100.0, spread=0.0004),
    ]

    evaluator = MockEvaluator([[setup], [], []])
    engine = BacktestEngine(config=config)
    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 1
    trade = result.trades[0]
    # Entry spread should be 0.0005: candle 1's own open (1.1020) + 0.00025 + 0.0001 = 1.10235
    assert trade.entry_price == pytest.approx(1.10235)
    # Exit spread should be 0.0004: 1.1050 - 0.0002 - 0.0001 = 1.1047
    assert trade.exit_price == pytest.approx(1.1047)


def test_commission_per_lot(state_builder: MarketStateBuilder) -> None:
    """Verifies that commission is calculated per-lot if commission_per_lot is provided."""
    config = BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.01,  # Risk $100
        spread=0.0,
        commission=5.0,  # Fallback commission
        slippage=0.0,
        commission_per_lot=2.5,  # $2.5 per unit/lot
    )

    setup = TradeSetup(
        setup_id="setup",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),  # sizing price 1.0990 (BUY -> low edge)
        stop_zone=(1.0960, 1.0970),  # stop price 1.0960 (sizing dist 0.0030)
        target_zone=(1.1060, 1.1070),
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )

    # Sizing risk distance is 0.0030 (entry_zone's low edge 1.0990 to stop
    # 1.0960 -- resolve_entry_price()'s BUY convention). Position size =
    # 100 / 0.0030 = 33333.33 units. The ACTUAL fill (candle 1's own open,
    # 1.1020, spread=0) is a different price than the sizing edge -- exactly
    # like live, where the order must be sized before the real fill price
    # is known.
    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1060, 1.1010, 1.1055),
    ]

    evaluator = MockEvaluator([[setup], [], []])
    engine = BacktestEngine(config=config)
    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 1
    trade = result.trades[0]
    pos_size = 100.0 / 0.0030
    expected_commission = 2.5 * pos_size
    expected_gross_pnl = (1.1060 - 1.1020) * pos_size  # TP - actual fill (candle 1's open)
    expected_net_pnl = expected_gross_pnl - expected_commission
    assert trade.pnl == pytest.approx(expected_net_pnl)


def test_negative_balance_clamp(state_builder: MarketStateBuilder) -> None:
    """Verifies that balance is clamped to 0.0, account is marked blown, and subsequent trades are blocked."""
    config = BacktestConfig(
        initial_balance=1000.0,
        risk_per_trade=0.90,  # Huge risk
        spread=0.0,
        commission=0.0,
        slippage=0.0,
    )

    # Setup 1 (will blow account)
    setup1 = TradeSetup(
        setup_id="setup1",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),  # SL 1.0980
        target_zone=(1.1050, 1.1060),
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )

    # Setup 2 (should not be entered)
    setup2 = TradeSetup(
        setup_id="setup2",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),
        target_zone=(1.1050, 1.1060),
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1) + timedelta(hours=1),
    )

    # Candles
    # Candle 0: Setup 1
    # Candle 1: Entry 1 at 1.1010. Position size is 900 / 0.0030 = 300,000 units (Wait: sl_price is 1.0980, distance 0.0030).
    # Candle 2: Drops to 1.0900. Exits at 1.0980. Loss is (1.0980 - 1.1010) * 300000 = -0.0030 * 300000 = -$900. Net balance is 100.
    # Wait, let's make it drop much further so loss exceeds initial balance. Let's make SL 1.0900.
    # entry at 1.1010, SL at 1.0900 (distance 0.0110). pos_size = 900 / 0.0110 = 81818.18.
    # Exits at 1.0900. Loss is -0.0110 * 81818.18 = -$900.
    # Let's just make the market gap to 1.0000. The SL exits at 1.0900. Loss is -$900. Still not negative.
    # Let's make risk_per_trade = 2.0 (risk 200% of balance!).
    # risk_amount = 2000. distance = 0.0030. pos_size = 2000 / 0.0030 = 666,666.67 units.
    # Loss = -0.0030 * 666666.67 = -$2000. Balance goes from $1000 to -$1000. Clamped to $0.
    config = BacktestConfig(
        initial_balance=1000.0,
        risk_per_trade=2.0,
        spread=0.0,
        commission=0.0,
        slippage=0.0,
    )

    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1020, 1.0970, 1.0985), # Hits SL at 1.0980
        _create_bar(3, 1.1020, 1.1030, 1.1015, 1.1020), # Setup 2 evaluation
        _create_bar(4, 1.1020, 1.1030, 1.1000, 1.1015), # setup 2 trigger
    ]

    evaluator = MockEvaluator([[setup1], [], [], [setup2], []])
    engine = BacktestEngine(config=config)
    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 1
    assert result.trades[0].pnl < -1000.0
    assert result.final_balance == 0.0
    assert result.account_blown is True
    assert result.blown_at_trade_index == 0


def test_max_daily_loss_circuit_breaker(state_builder: MarketStateBuilder) -> None:
    """Verifies that reaching the daily loss limit blocks new trades for the day and resets the next day."""
    config = BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.06,  # 6% risk per trade ($600 risk)
        spread=0.0,
        commission=0.0,
        slippage=0.0,
        max_daily_loss_pct=0.05,  # 5% daily loss limit ($500)
    )

    # Day 1 trade (loss)
    setup_day1 = TradeSetup(
        setup_id="setup_day1",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),  # SL 1.0980
        target_zone=(1.1050, 1.1060),
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1, 8, 0),
    )


    # Day 2 trade (should be allowed)
    setup_day2 = TradeSetup(
        setup_id="setup_day2",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),
        target_zone=(1.1050, 1.1060),
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 2, 9, 0),
    )

    # Candles
    # Day 1:
    # 08:00 - Setup Day 1
    # 08:15 - Enter Day 1
    # 08:30 - Hits SL (Day 1 realized loss = -$600, limit is -$500)
    # 14:00 - Setup Day 1 blocked
    # 14:15 - Day 1 blocked trigger attempt
    # Day 2:
    # 09:00 - Setup Day 2
    # 09:15 - Enter Day 2
    # 09:30 - Exits winning
    candles = [
        Bar(timestamp=datetime(2026, 1, 1, 8, 0), open=1.1020, high=1.1030, low=1.1015, close=1.1020, volume=100.0),
        Bar(timestamp=datetime(2026, 1, 1, 8, 15), open=1.1020, high=1.1030, low=1.1000, close=1.1015, volume=100.0),
        Bar(timestamp=datetime(2026, 1, 1, 8, 30), open=1.1015, high=1.1020, low=1.0970, close=1.0985, volume=100.0),
        Bar(timestamp=datetime(2026, 1, 1, 14, 0), open=1.1020, high=1.1030, low=1.1015, close=1.1020, volume=100.0),
        Bar(timestamp=datetime(2026, 1, 1, 14, 15), open=1.1020, high=1.1030, low=1.1000, close=1.1015, volume=100.0),
        # New Day
        Bar(timestamp=datetime(2026, 1, 2, 9, 0), open=1.1020, high=1.1030, low=1.1015, close=1.1020, volume=100.0),
        Bar(timestamp=datetime(2026, 1, 2, 9, 15), open=1.1020, high=1.1030, low=1.1000, close=1.1015, volume=100.0),
        Bar(timestamp=datetime(2026, 1, 2, 9, 30), open=1.1015, high=1.1060, low=1.1010, close=1.1055, volume=100.0),
    ]

    evaluator = MockEvaluator([[setup_day1], [setup_day2]])
    engine = BacktestEngine(config=config)
    result = engine.run(candles, evaluator, state_builder)


    # Day 1 blocked trade should not be in results, only Day 1 SL and Day 2 WIN trades.
    assert len(result.trades) == 2
    assert result.trades[0].setup_id == "setup_day1"
    assert result.trades[1].setup_id == "setup_day2"
    assert result.daily_loss_limit_hits == 1


def test_max_equity_drawdown_circuit_breaker(state_builder: MarketStateBuilder) -> None:
    """Verifies that exceeding the max equity drawdown stops the simulation immediately."""
    config = BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.15,  # 15% risk ($1500)
        spread=0.0,
        commission=0.0,
        slippage=0.0,
        max_equity_drawdown_pct=0.10,  # 10% max drawdown limit
    )

    setup1 = TradeSetup(
        setup_id="setup1",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),  # SL 1.0980
        target_zone=(1.1050, 1.1060),
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )

    setup2 = TradeSetup(
        setup_id="setup2",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),
        target_zone=(1.1050, 1.1060),
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1) + timedelta(hours=1),
    )

    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1020, 1.0970, 1.0985), # Loss of 15% ($1500) -> balance is $8500 (drawdown 15% > 10%)
        _create_bar(3, 1.1020, 1.1030, 1.1015, 1.1020), # If not stopped, Setup 2 will evaluate here
    ]

    evaluator = MockEvaluator([[setup1], [], [], [setup2]])
    engine = BacktestEngine(config=config)
    result = engine.run(candles, evaluator, state_builder)

    # Should exit immediately after Trade 1 closes, Trade 2 setup should never be evaluated (evaluator.call_count < 4)
    assert len(result.trades) == 1
    assert result.stopped_early is True
    assert result.stop_reason is not None
    assert "Max equity drawdown limit" in result.stop_reason
    assert evaluator.call_count <= 3


def test_round_trip_spread_cost_is_charged_once(state_builder: MarketStateBuilder) -> None:
    """Verifies that the spread is charged exactly once (half on entry, half on exit) over a round-trip."""
    config = BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.01,  # 1% risk ($100 risk amount)
        spread=0.0010,       # 10 pips spread
        commission=0.0,
        slippage=0.0,
        max_holding_bars=None,
    )

    setup = TradeSetup(
        setup_id="setup_spread_test",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1000),      # limit_price = 1.1000
        stop_zone=(1.0900, 1.0910),       # sl = 1.0900
        target_zone=(1.1100, 1.1110),     # tp = 1.1100
        confidence_score=1.0,
        confluence=[],
        trigger_reason="Test OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )

    evaluator = MockEvaluator([[setup], [], []])
    engine = BacktestEngine(config=config)

    # Candles. Entry-fill bar's open (candle 1) is set to entry_zone's low
    # edge (1.0990, the BUY sizing price) so the sizing reference and actual
    # MARKET fill coincide.
    # Candle 0: triggers signal
    # Candle 1: MARKET fill at this bar's own open (1.0990)
    #           Expected entry_price = 1.0990 + spread/2 = 1.0995
    #           Position size = 100 / (1.0990 - 1.0900) = 100 / 0.0090 = 11111.1111
    # Candle 2: High hits TP (1.1120)
    #           Expected exit_price = 1.1100 - spread/2 = 1.1095
    #           Expected gross PnL = (1.1095 - 1.0995) * 11111.1111 = 0.0100 * 11111.1111 = 111.1111
    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.0990, 1.1030, 1.0990, 1.1015),
        _create_bar(2, 1.1015, 1.1120, 1.1010, 1.1055),
    ]

    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 1
    trade = result.trades[0]

    # Verify entry and exit prices
    assert abs(trade.entry_price - 1.0995) < 1e-7
    assert abs(trade.exit_price - 1.1095) < 1e-7

    # Verify position sizing
    expected_pos_size = 100.0 / (1.0990 - 1.0900)
    assert abs(trade.position_size - expected_pos_size) < 1e-7

    # Verify gross/net PnL (round-trip difference is exactly 0.0100 price units)
    expected_pnl = (1.1095 - 1.0995) * expected_pos_size
    assert abs(trade.pnl - expected_pnl) < 1e-7


def test_pending_setup_always_fills_on_the_immediate_next_bar(state_builder: MarketStateBuilder) -> None:
    """Regression test: a setup fills UNCONDITIONALLY as a MARKET order on
    the very next bar's own open -- it never waits across multiple bars for
    price to revisit its entry_zone. That "resting limit order" mechanic
    (and the config knob that used to bound how many bars it could wait,
    BacktestConfig.pending_order_expiry_bars) does not exist anywhere in
    this codebase's live execution path: execution/trade_manager.py's
    open_trade() always places an immediate MARKET order (see
    execution/fill_simulator.simulate_market_fill()), so BacktestEngine
    must not simulate anything else.
    """
    setup = TradeSetup(
        setup_id="setup_market_fill_test",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1000),
        stop_zone=(1.0900, 1.0910),
        target_zone=(1.1100, 1.1110),
        confidence_score=1.0,
        confluence=[],
        trigger_reason="Test OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )

    # Candle 1's own low (1.1050) never comes anywhere near entry_zone
    # (1.0990-1.1000) -- under the old "resting limit order" model this
    # setup would never have filled on this bar at all (it would have
    # needed a LATER bar to dip back down to the zone). Under the current
    # MARKET-order model it fills regardless, at candle 1's own open.
    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1055, 1.1020, 1.1050),
        _create_bar(2, 1.1015, 1.1120, 1.1010, 1.1055),  # High hits TP
    ]

    config = BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.01,
        spread=0.0,
        commission=0.0,
        slippage=0.0,
        max_holding_bars=None,
    )
    evaluator = MockEvaluator([[setup], [], []])
    engine = BacktestEngine(config=config)
    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.result == TradeResult.WIN
    assert abs(trade.entry_price - 1.1020) < 1e-7  # candle 1's own open, not any zone edge
    assert abs(trade.exit_price - 1.1100) < 1e-7
    assert trade.entry_bar_index == 1
    assert trade.exit_bar_index == 2


# --- Bug #25: multiple strategies proposing setups on the same bar ------------------


def _make_setup(setup_id: str, direction: SignalDirection = SignalDirection.BUY) -> TradeSetup:
    return TradeSetup(
        setup_id=setup_id,
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=direction,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),
        target_zone=(1.1050, 1.1060),
        confidence_score=0.9,
        confluence=[],
        trigger_reason=setup_id,
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )


def test_conflicting_setups_on_same_bar_are_counted_and_first_wins(
    base_config: BacktestConfig, state_builder: MarketStateBuilder
) -> None:
    """Two strategies proposing a setup on the same bar: only setups[0] is
    taken (unchanged behavior), but the drop is now counted, not silent.
    """
    setup_a = _make_setup("setup_a")
    setup_b = _make_setup("setup_b", direction=SignalDirection.SELL)

    evaluator = MockEvaluator([[setup_a, setup_b], [], []])
    engine = BacktestEngine(config=base_config)

    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1060, 1.1010, 1.1055),
    ]

    result = engine.run(candles, evaluator, state_builder)

    assert result.conflicting_setups_dropped == 1
    assert len(result.trades) == 1
    assert result.trades[0].setup_id == "setup_a"


def test_no_conflict_counted_when_single_setup_proposed(
    base_config: BacktestConfig, state_builder: MarketStateBuilder
) -> None:
    setup = _make_setup("setup_only")
    evaluator = MockEvaluator([[setup], [], []])
    engine = BacktestEngine(config=base_config)

    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1060, 1.1010, 1.1055),
    ]

    result = engine.run(candles, evaluator, state_builder)

    assert result.conflicting_setups_dropped == 0


def test_conflict_policy_log_and_first_logs_a_warning(
    base_config: BacktestConfig, state_builder: MarketStateBuilder, caplog
) -> None:
    setup_a = _make_setup("setup_a")
    setup_b = _make_setup("setup_b", direction=SignalDirection.SELL)

    evaluator = MockEvaluator([[setup_a, setup_b], [], []])
    engine = BacktestEngine(config=base_config, conflict_policy="log_and_first")

    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1060, 1.1010, 1.1055),
    ]

    with caplog.at_level("WARNING"):
        result = engine.run(candles, evaluator, state_builder)

    assert result.conflicting_setups_dropped == 1
    assert any("conflicting" in r.message.lower() for r in caplog.records)


def test_conflict_policy_first_does_not_log(
    base_config: BacktestConfig, state_builder: MarketStateBuilder, caplog
) -> None:
    setup_a = _make_setup("setup_a")
    setup_b = _make_setup("setup_b", direction=SignalDirection.SELL)

    evaluator = MockEvaluator([[setup_a, setup_b], [], []])
    engine = BacktestEngine(config=base_config)  # default conflict_policy="first"

    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1060, 1.1010, 1.1055),
    ]

    with caplog.at_level("WARNING"):
        result = engine.run(candles, evaluator, state_builder)

    assert result.conflicting_setups_dropped == 1  # still counted...
    assert len(caplog.records) == 0  # ...but not logged under the default policy


# --- Audit #17: margin/leverage-aware position sizing ---------------------


def _make_index_setup(
    setup_id: str = "setup_margin",
    direction: SignalDirection = SignalDirection.BUY,
) -> TradeSetup:
    """A high-notional setup (index-scale prices, tight stop) used to exercise
    margin checking: entry ~25010, stop distance 10 -> pos_size 10 units at
    1% risk on a $10,000 balance -> notional ~$250,100.
    """
    if direction == SignalDirection.BUY:
        entry_zone = (24990.0, 25010.0)  # limit_price = 25010
        stop_zone = (25000.0, 25005.0)  # sl_price (BUY uses sl_low) = 25000, distance 10
        target_zone = (25200.0, 25210.0)
    else:
        entry_zone = (24990.0, 25010.0)  # limit_price = entry_low = 24990
        stop_zone = (24995.0, 25000.0)  # sl_price (SELL uses sl_high) = 25000, distance 10
        target_zone = (24790.0, 24800.0)
    return TradeSetup(
        setup_id=setup_id,
        symbol="USTEC",
        timeframe=Timeframe.M15,
        direction=direction,
        entry_zone=entry_zone,
        stop_zone=stop_zone,
        target_zone=target_zone,
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Test high-notional setup",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )


def test_margin_check_default_none_is_a_no_op(state_builder: MarketStateBuilder) -> None:
    """Differential/regression test: BacktestConfig.leverage defaults to None,
    which must reproduce the exact pre-change behavior -- a high-notional
    trade that would be rejected under a real leverage cap still opens
    normally when leverage is unset, and margin_rejected_setups stays 0.
    This proves the new margin gate is a true no-op for every existing
    config/test (none of which set leverage), including every prior
    scenario in this file and any backtest run (e.g. the Midline Sweep
    USTEC OOS run) that predates this change.
    """
    setup = _make_index_setup()
    config = BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.01,
        spread=0.0,
        commission=0.0,
        slippage=0.0,
        # leverage intentionally omitted -> defaults to None
    )
    evaluator = MockEvaluator([[setup], [], []])
    engine = BacktestEngine(config=config)

    candles = [
        _create_bar(0, 25005.0, 25015.0, 25000.0, 25005.0),
        _create_bar(1, 25005.0, 25020.0, 25000.0, 25010.0),  # low=25000 <= limit_price 25010 -> fills
        _create_bar(2, 25010.0, 25210.0, 25005.0, 25050.0),  # hits TP
    ]

    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 1
    assert result.trades[0].result == TradeResult.WIN
    assert result.margin_rejected_setups == 0


def test_margin_rejects_position_exceeding_leverage_capacity(
    state_builder: MarketStateBuilder,
) -> None:
    """At leverage=20 (real ESMA-style index cap), a high-notional setup
    (~$250,100 notional against a $10,000 balance -> required_margin
    ~$12,505 > balance) must be silently rejected: no trade opens, the
    balance is untouched, and margin_rejected_setups is incremented.
    """
    setup = _make_index_setup()
    config = BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.01,
        spread=0.0,
        commission=0.0,
        slippage=0.0,
        leverage=20.0,
        contract_size=1.0,
    )
    evaluator = MockEvaluator([[setup], [], []])
    engine = BacktestEngine(config=config)

    candles = [
        _create_bar(0, 25005.0, 25015.0, 25000.0, 25005.0),
        _create_bar(1, 25005.0, 25020.0, 25000.0, 25010.0),  # would fill, but margin check rejects
        _create_bar(2, 25010.0, 25210.0, 25005.0, 25050.0),
    ]

    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 0
    assert result.margin_rejected_setups == 1
    assert result.final_balance == 10000.0
    assert result.account_blown is False


def test_margin_rejects_sell_position_exceeding_leverage_capacity(
    state_builder: MarketStateBuilder,
) -> None:
    """Same margin rejection, mirrored for the SELL fill branch."""
    setup = _make_index_setup(direction=SignalDirection.SELL)
    config = BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.01,
        spread=0.0,
        commission=0.0,
        slippage=0.0,
        leverage=20.0,
        contract_size=1.0,
    )
    evaluator = MockEvaluator([[setup], [], []])
    engine = BacktestEngine(config=config)

    candles = [
        _create_bar(0, 25005.0, 25015.0, 25000.0, 25005.0),
        _create_bar(1, 24985.0, 25000.0, 24985.0, 24995.0),  # high=25000 >= limit_price 24990 -> would fill
        _create_bar(2, 24995.0, 25000.0, 24805.0, 24810.0),
    ]

    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 0
    assert result.margin_rejected_setups == 1
    assert result.final_balance == 10000.0


def test_margin_allows_position_within_leverage_capacity(
    base_config: BacktestConfig, state_builder: MarketStateBuilder
) -> None:
    """Sanity/boundary check: a normal low-notional FX trade (from
    test_backtest_winning_trade's exact scenario) must still open even with
    a leverage cap set, proving the margin gate only rejects setups that
    genuinely exceed the account's margin capacity.
    """
    setup = TradeSetup(
        setup_id="setup_win",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),
        target_zone=(1.1050, 1.1060),
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )

    config = BacktestConfig(
        initial_balance=base_config.initial_balance,
        risk_per_trade=base_config.risk_per_trade,
        spread=base_config.spread,
        commission=base_config.commission,
        slippage=base_config.slippage,
        max_holding_bars=base_config.max_holding_bars,
        leverage=20.0,
        contract_size=1.0,
    )
    evaluator = MockEvaluator([[setup], [], []])
    engine = BacktestEngine(config=config)

    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1060, 1.1010, 1.1055),
    ]

    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 1
    assert result.trades[0].result == TradeResult.WIN
    assert result.margin_rejected_setups == 0
def test_fully_resolved_backtest_is_unaffected_by_data_end_force_close(
    base_config: BacktestConfig, state_builder: MarketStateBuilder
) -> None:
    """Differential regression for Bug #54: a backtest where every position closes via
    SL/TP well before the data ends (test_backtest_winning_trade's exact scenario) must
    produce identical trade count/result/pnl and force_closed_at_data_end == 0 -- the
    data-end force-close introduced for Bug #54 must never touch an already-closed trade.
    """
    setup = TradeSetup(
        setup_id="setup_win",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0980, 1.0990),
        target_zone=(1.1050, 1.1060),
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )

    evaluator = MockEvaluator([[setup], [], []])
    engine = BacktestEngine(config=base_config)

    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1060, 1.1010, 1.1055),
    ]

    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.result == TradeResult.WIN
    assert trade.entry_price == 1.1020
    assert trade.exit_price == 1.1050
    assert trade.pnl > 0.0
    assert result.total_profit > 0.0
    assert result.win_rate == 1.0
    assert result.profit_factor == float("inf")
    assert result.force_closed_at_data_end == 0


def test_open_position_force_closed_at_data_end(state_builder: MarketStateBuilder) -> None:
    """Bug #54: a position still open when the candle series ends (no max_holding_bars,
    SL/TP never touched) must be force-closed at the last candle's close price and
    counted, instead of silently vanishing from every reported metric.
    """
    config = BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.01,
        spread=0.0,
        commission=0.0,
        slippage=0.0,
        max_holding_bars=None,
    )

    # SL/TP are set far outside the price range below, so neither is ever hit, and
    # max_holding_bars is None so there is no expiry either -- the position is still
    # open when the candle list simply runs out.
    setup = TradeSetup(
        setup_id="setup_open_at_end",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.0990, 1.1010),
        stop_zone=(1.0500, 1.0510),  # far below any candle low
        target_zone=(1.2000, 1.2010),  # far above any candle high
        confidence_score=0.9,
        confluence=[],
        trigger_reason="Bullish OB",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1),
    )

    # Candle 0: triggers signal (pending setup proposed)
    # Candle 1: MARKET fill at this bar's own open, 1.1020 (bars_held starts at 0 this bar)
    # Candle 2: bars_held becomes 1, still open
    # Candle 3: bars_held becomes 2, still open -- data ends here, position still open
    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1025, 1.1010, 1.1018),
        _create_bar(3, 1.1018, 1.1028, 1.1012, 1.1022),
    ]

    evaluator = MockEvaluator([[setup], [], [], []])
    engine = BacktestEngine(config=config)
    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.result == TradeResult.EXPIRED
    assert trade.bars_held == 2
    assert trade.entry_price == 1.1020
    assert trade.exit_price == pytest.approx(1.1022)  # candle 3's close, no spread/slippage configured
    assert trade.exit_time == candles[3].timestamp
    assert trade.exit_bar_index == 3
    assert trade.pnl == pytest.approx((1.1022 - 1.1020) * trade.position_size)

    # The trade's outcome must now be visible in every top-level aggregate.
    assert result.total_profit == pytest.approx(trade.pnl)
    assert result.final_balance == pytest.approx(10000.0 + trade.pnl)
    assert result.force_closed_at_data_end == 1


def test_no_force_close_when_no_position_was_ever_open(
    base_config: BacktestConfig, state_builder: MarketStateBuilder
) -> None:
    """Bug #54 edge case: a backtest with zero setups/trades must not spuriously
    force-close anything (active_trade is always None, so the new code path must
    be a no-op)."""
    evaluator = MockEvaluator([[], [], []])
    engine = BacktestEngine(config=base_config)

    candles = [
        _create_bar(0, 1.1020, 1.1030, 1.1015, 1.1020),
        _create_bar(1, 1.1020, 1.1030, 1.1000, 1.1015),
        _create_bar(2, 1.1015, 1.1060, 1.1010, 1.1055),
    ]

    result = engine.run(candles, evaluator, state_builder)

    assert len(result.trades) == 0
    assert result.force_closed_at_data_end == 0
