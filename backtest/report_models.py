"""Data models for historical simulation reporting and analytics."""

from dataclasses import dataclass
from typing import Any

from core.models import SignalDirection


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    """Aggregated professional backtesting performance statistics."""

    total_trades: int
    winning_trades: int
    losing_trades: int
    expired_trades: int
    win_rate: float
    loss_rate: float
    profit_factor: float
    expectancy: float
    average_r: float
    average_win: float
    average_loss: float
    largest_win: float
    largest_loss: float
    gross_profit: float
    gross_loss: float
    net_profit: float
    max_drawdown: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    median_r: float = 0.0
    average_holding_bars: float = 0.0
    median_holding_bars: float = 0.0
    average_winner_duration: float = 0.0
    average_loser_duration: float = 0.0
    max_holding_bars: int = 0
    min_holding_bars: int = 0
    average_drawdown: float = 0.0
    longest_drawdown_duration: float = 0.0
    recovery_time: float = 0.0
    drawdown_distribution: dict[str, int] | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    calmar_ratio: float | None = None
    buy_trades: int = 0
    sell_trades: int = 0
    tp_exits: int = 0
    sl_exits: int = 0
    expired_exits: int = 0
    average_rr: float = 0.0
    median_rr: float = 0.0
    largest_winning_streak: int = 0
    largest_losing_streak: int = 0
    monthly_statistics: list[dict[str, Any]] | None = None



@dataclass(frozen=True, slots=True)
class DirectionMetrics:
    """Simulated performance metrics broken down by BUY/SELL direction."""

    direction: SignalDirection
    trades: int
    wins: int
    losses: int
    win_rate: float
    profit_factor: float
    average_r: float
    net_profit: float


@dataclass(frozen=True, slots=True)
class SetupMetrics:
    """Simulated performance metrics grouped by strategy setup and trigger rules."""

    setup_id: str
    strategy_name: str
    trigger_reason: str
    trades: int
    wins: int
    losses: int
    win_rate: float
    profit_factor: float
    average_r: float
    net_profit: float
