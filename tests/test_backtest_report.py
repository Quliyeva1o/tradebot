"""Tests for the BacktestReportGenerator analytics layer."""

from datetime import datetime

from backtest.models import BacktestResult, BacktestTrade, TradeResult
from backtest.report import BacktestReportGenerator
from core.models import SignalDirection


def _create_trade(
    pnl: float,
    result: TradeResult,
    direction: SignalDirection = SignalDirection.BUY,
    r_multiple: float = 1.0,
    setup_id: str = "setup_1",
    trigger_reason: str = "reason_1",
    strategy_name: str = "strat_1",
    exit_time: datetime = datetime(2026, 1, 15),
) -> BacktestTrade:
    """Helper to generate a BacktestTrade object."""
    return BacktestTrade(
        entry_time=datetime(2026, 1, 1),
        exit_time=exit_time,
        direction=direction,
        entry_price=1.1000,
        exit_price=1.1050 if direction == SignalDirection.BUY else 1.0950,
        stop_loss=1.0950 if direction == SignalDirection.BUY else 1.1050,
        take_profit=1.1150 if direction == SignalDirection.BUY else 1.0850,
        result=result,
        pnl=pnl,
        r_multiple=r_multiple,
        symbol="EURUSD",
        setup_id=setup_id,
        strategy_name=strategy_name,
        trigger_reason=trigger_reason,
        confidence_score=0.95,
    )


def test_empty_result() -> None:
    """Verifies that an empty backtest result outputs correct zeroed metrics."""
    result = BacktestResult(
        trades=(),
        total_profit=0.0,
        win_rate=0.0,
        max_drawdown=0.0,
        profit_factor=1.0,
        initial_balance=10000.0,
        final_balance=10000.0,
    )
    generator = BacktestReportGenerator()
    metrics = generator.generate(result)

    assert metrics.total_trades == 0
    assert metrics.winning_trades == 0
    assert metrics.losing_trades == 0
    assert metrics.expired_trades == 0
    assert metrics.win_rate == 0.0
    assert metrics.loss_rate == 0.0
    assert metrics.net_profit == 0.0
    assert metrics.profit_factor == 1.0
    assert metrics.expectancy == 0.0
    assert metrics.average_r == 0.0
    assert metrics.average_win == 0.0
    assert metrics.average_loss == 0.0
    assert metrics.largest_win == 0.0
    assert metrics.largest_loss == 0.0
    assert metrics.gross_profit == 0.0
    assert metrics.gross_loss == 0.0
    assert metrics.max_drawdown == 0.0
    assert metrics.max_consecutive_wins == 0
    assert metrics.max_consecutive_losses == 0


def test_all_winning_trades() -> None:
    """Verifies analytics for a pure winning backtest sequence."""
    trades = (
        _create_trade(pnl=100.0, result=TradeResult.WIN, r_multiple=2.0),
        _create_trade(pnl=200.0, result=TradeResult.WIN, r_multiple=4.0),
    )
    result = BacktestResult(
        trades=trades,
        total_profit=300.0,
        win_rate=1.0,
        max_drawdown=0.0,
        profit_factor=float("inf"),
        initial_balance=10000.0,
        final_balance=10300.0,
    )
    generator = BacktestReportGenerator()
    metrics = generator.generate(result)

    assert metrics.total_trades == 2
    assert metrics.winning_trades == 2
    assert metrics.losing_trades == 0
    assert metrics.win_rate == 1.0
    assert metrics.loss_rate == 0.0
    assert metrics.net_profit == 300.0
    assert metrics.profit_factor == float("inf")
    assert metrics.expectancy == 150.0  # Average win is 150, loss is 0. 1.0 * 150 - 0 = 150
    assert metrics.average_r == 3.0
    assert metrics.largest_win == 200.0
    assert metrics.largest_loss == 0.0
    assert metrics.max_consecutive_wins == 2
    assert metrics.max_consecutive_losses == 0


def test_all_losing_trades() -> None:
    """Verifies analytics for a pure losing backtest sequence."""
    trades = (
        _create_trade(pnl=-50.0, result=TradeResult.LOSS, r_multiple=-1.0),
        _create_trade(pnl=-150.0, result=TradeResult.LOSS, r_multiple=-3.0),
    )
    result = BacktestResult(
        trades=trades,
        total_profit=-200.0,
        win_rate=0.0,
        max_drawdown=0.02,
        profit_factor=0.0,
        initial_balance=10000.0,
        final_balance=9800.0,
    )
    generator = BacktestReportGenerator()
    metrics = generator.generate(result)

    assert metrics.total_trades == 2
    assert metrics.winning_trades == 0
    assert metrics.losing_trades == 2
    assert metrics.win_rate == 0.0
    assert metrics.loss_rate == 1.0
    assert metrics.net_profit == -200.0
    assert metrics.profit_factor == 0.0
    assert metrics.expectancy == -100.0  # win is 0, loss average is 100. 0 - 1.0 * 100 = -100
    assert metrics.largest_win == 0.0
    assert metrics.largest_loss == 150.0
    assert metrics.max_consecutive_wins == 0
    assert metrics.max_consecutive_losses == 2


def test_mixed_trades_expectancy_and_pf() -> None:
    """Verifies calculations of profit factor and expectancy for mixed win/loss trades."""
    trades = (
        _create_trade(pnl=200.0, result=TradeResult.WIN),
        _create_trade(pnl=-100.0, result=TradeResult.LOSS),
        _create_trade(pnl=300.0, result=TradeResult.WIN),
        _create_trade(pnl=-200.0, result=TradeResult.LOSS),
    )
    result = BacktestResult(
        trades=trades,
        total_profit=200.0,
        win_rate=0.5,
        max_drawdown=0.01,
        profit_factor=1.6667,
        initial_balance=10000.0,
        final_balance=10200.0,
    )
    generator = BacktestReportGenerator()
    metrics = generator.generate(result)

    assert metrics.total_trades == 4
    assert metrics.winning_trades == 2
    assert metrics.losing_trades == 2
    assert metrics.win_rate == 0.5
    assert metrics.loss_rate == 0.5
    assert metrics.gross_profit == 500.0
    assert metrics.gross_loss == 300.0
    assert metrics.net_profit == 200.0
    # profit_factor = 500 / 300 = 1.66666...
    assert abs(metrics.profit_factor - 1.6667) < 0.001

    # expectancy: (win_rate * average_win) - (loss_rate * average_loss)
    # average_win = 250, average_loss = 150
    # expectancy = 0.5 * 250 - 0.5 * 150 = 125 - 75 = 50
    assert metrics.expectancy == 50.0
    assert metrics.largest_win == 300.0
    assert metrics.largest_loss == 200.0


def test_drawdown_calculation() -> None:
    """Verifies drawdown calculation from the simulated equity curve peaks-to-troughs."""
    trades = (
        _create_trade(pnl=2000.0, result=TradeResult.WIN),  # balance: 12000, peak: 12000
        _create_trade(
            pnl=-3000.0, result=TradeResult.LOSS
        ),  # balance: 9000, peak: 12000. DD: 3000/12000 = 25%
        _create_trade(pnl=4000.0, result=TradeResult.WIN),  # balance: 13000, peak: 13000
        _create_trade(
            pnl=-1300.0, result=TradeResult.LOSS
        ),  # balance: 11700, peak: 13000. DD: 1300/13000 = 10%
    )
    result = BacktestResult(
        trades=trades,
        total_profit=1700.0,
        win_rate=0.5,
        max_drawdown=0.25,
        profit_factor=1.395,
        initial_balance=10000.0,
        final_balance=11700.0,
    )
    generator = BacktestReportGenerator()
    metrics = generator.generate(result)

    assert metrics.max_drawdown == 0.25


def test_buy_vs_sell_metrics() -> None:
    """Verifies direction metrics calculation separates BUY and SELL trades correctly."""
    trades = (
        _create_trade(
            pnl=200.0, result=TradeResult.WIN, direction=SignalDirection.BUY, r_multiple=2.0
        ),
        _create_trade(
            pnl=-100.0, result=TradeResult.LOSS, direction=SignalDirection.BUY, r_multiple=-1.0
        ),
        _create_trade(
            pnl=300.0, result=TradeResult.WIN, direction=SignalDirection.SELL, r_multiple=3.0
        ),
    )
    result = BacktestResult(
        trades=trades,
        total_profit=400.0,
        win_rate=0.6667,
        max_drawdown=0.01,
        profit_factor=5.0,
        initial_balance=10000.0,
        final_balance=10400.0,
    )
    generator = BacktestReportGenerator()
    dir_metrics = generator.calculate_direction_metrics(result)

    assert dir_metrics[SignalDirection.BUY].direction == SignalDirection.BUY
    assert dir_metrics[SignalDirection.BUY].trades == 2
    assert dir_metrics[SignalDirection.BUY].wins == 1
    assert dir_metrics[SignalDirection.BUY].losses == 1
    assert dir_metrics[SignalDirection.BUY].win_rate == 0.5
    assert dir_metrics[SignalDirection.BUY].profit_factor == 2.0
    assert dir_metrics[SignalDirection.BUY].average_r == 0.5
    assert dir_metrics[SignalDirection.BUY].net_profit == 100.0

    assert dir_metrics[SignalDirection.SELL].direction == SignalDirection.SELL
    assert dir_metrics[SignalDirection.SELL].trades == 1
    assert dir_metrics[SignalDirection.SELL].wins == 1
    assert dir_metrics[SignalDirection.SELL].losses == 0
    assert dir_metrics[SignalDirection.SELL].win_rate == 1.0
    assert dir_metrics[SignalDirection.SELL].profit_factor == float("inf")
    assert dir_metrics[SignalDirection.SELL].average_r == 3.0
    assert dir_metrics[SignalDirection.SELL].net_profit == 300.0


def test_consecutive_streaks_detection() -> None:
    """Verifies maximum consecutive winning/losing streak detection."""
    trades = (
        _create_trade(pnl=-50.0, result=TradeResult.LOSS),
        _create_trade(pnl=100.0, result=TradeResult.WIN),
        _create_trade(pnl=-50.0, result=TradeResult.LOSS),
        _create_trade(pnl=-50.0, result=TradeResult.LOSS),
        _create_trade(pnl=-50.0, result=TradeResult.LOSS),
        _create_trade(pnl=100.0, result=TradeResult.WIN),
    )
    result = BacktestResult(
        trades=trades,
        total_profit=0.0,
        win_rate=0.33,
        max_drawdown=0.05,
        profit_factor=1.0,
        initial_balance=10000.0,
        final_balance=10000.0,
    )
    generator = BacktestReportGenerator()
    metrics = generator.generate(result)

    assert metrics.max_consecutive_losses == 3
    assert metrics.max_consecutive_wins == 1


def test_setup_metrics_grouping() -> None:
    """Verifies that trades are grouped correctly by primary setup_id."""
    trades = (
        _create_trade(
            pnl=200.0,
            result=TradeResult.WIN,
            setup_id="OB_Bullish",
            trigger_reason="Bullish OB",
            strategy_name="BullishOB",
            r_multiple=2.0,
        ),
        _create_trade(
            pnl=-100.0,
            result=TradeResult.LOSS,
            setup_id="OB_Bullish",
            trigger_reason="Bullish OB",
            strategy_name="BullishOB",
            r_multiple=-1.0,
        ),
        _create_trade(
            pnl=300.0,
            result=TradeResult.WIN,
            setup_id="FVG_Bullish",
            trigger_reason="Bullish FVG",
            strategy_name="BullishFVG",
            r_multiple=3.0,
        ),
    )
    result = BacktestResult(
        trades=trades,
        total_profit=400.0,
        win_rate=0.6667,
        max_drawdown=0.01,
        profit_factor=5.0,
        initial_balance=10000.0,
        final_balance=10400.0,
    )
    generator = BacktestReportGenerator()
    setup_stats = generator.calculate_setup_metrics(result)

    assert len(setup_stats) == 2
    assert setup_stats["OB_Bullish"].setup_id == "OB_Bullish"
    assert setup_stats["OB_Bullish"].trades == 2
    assert setup_stats["OB_Bullish"].wins == 1
    assert setup_stats["OB_Bullish"].losses == 1
    assert setup_stats["OB_Bullish"].win_rate == 0.5
    assert setup_stats["OB_Bullish"].profit_factor == 2.0
    assert setup_stats["OB_Bullish"].average_r == 0.5
    assert setup_stats["OB_Bullish"].net_profit == 100.0
    assert setup_stats["OB_Bullish"].trigger_reason == "Bullish OB"
    assert setup_stats["OB_Bullish"].strategy_name == "BullishOB"

    assert setup_stats["FVG_Bullish"].setup_id == "FVG_Bullish"
    assert setup_stats["FVG_Bullish"].trades == 1
    assert setup_stats["FVG_Bullish"].wins == 1
    assert setup_stats["FVG_Bullish"].losses == 0
    assert setup_stats["FVG_Bullish"].win_rate == 1.0
    assert setup_stats["FVG_Bullish"].profit_factor == float("inf")
    assert setup_stats["FVG_Bullish"].average_r == 3.0
    assert setup_stats["FVG_Bullish"].net_profit == 300.0
    assert setup_stats["FVG_Bullish"].trigger_reason == "Bullish FVG"
    assert setup_stats["FVG_Bullish"].strategy_name == "BullishFVG"


def test_monthly_returns() -> None:
    """Verifies that monthly returns are aggregated correctly by calendar month."""
    trades = (
        _create_trade(pnl=150.0, result=TradeResult.WIN, exit_time=datetime(2026, 1, 10)),
        _create_trade(pnl=200.0, result=TradeResult.WIN, exit_time=datetime(2026, 1, 20)),
        _create_trade(pnl=-50.0, result=TradeResult.LOSS, exit_time=datetime(2026, 2, 5)),
    )
    result = BacktestResult(
        trades=trades,
        total_profit=300.0,
        win_rate=0.6667,
        max_drawdown=0.0,
        profit_factor=7.0,
        initial_balance=10000.0,
        final_balance=10300.0,
    )
    generator = BacktestReportGenerator()
    returns = generator.calculate_monthly_returns(result)

    assert returns["2026-01"] == 350.0
    assert returns["2026-02"] == -50.0


def test_equity_curve() -> None:
    """Verifies that the equity curve returns expected chronological balances."""
    trades = (
        _create_trade(pnl=150.0, result=TradeResult.WIN),
        _create_trade(pnl=-70.0, result=TradeResult.LOSS),
        _create_trade(pnl=240.0, result=TradeResult.WIN),
    )
    result = BacktestResult(
        trades=trades,
        total_profit=320.0,
        win_rate=0.6667,
        max_drawdown=0.0,
        profit_factor=5.57,
        initial_balance=10000.0,
        final_balance=10320.0,
    )
    generator = BacktestReportGenerator()
    curve = generator.calculate_equity_curve(result)

    assert curve == [10000.0, 10150.0, 10080.0, 10320.0]
