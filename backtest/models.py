"""Data models for historical backtesting simulation."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from core.models import SignalDirection


class TradeResult(Enum):
    """Execution outcome of a completed backtest trade."""

    WIN = "WIN"
    LOSS = "LOSS"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class BacktestConfig:
    """Configuration metrics for the historical backtester."""

    initial_balance: float
    risk_per_trade: float  # e.g., 0.01 for 1% risk per trade
    spread: float  # Full bid/ask spread in price units (Ask - Bid). Half of this is applied on entry, half on exit, so the total round-trip cost equals exactly one spread width. Slippage is applied in full on both legs independently, since each execution incurs its own slippage.
    commission: float  # flat fee per trade transaction
    slippage: float
    max_holding_bars: int | None = None
    commission_per_lot: float | None = None
    max_daily_loss_pct: float | None = None
    max_equity_drawdown_pct: float | None = None
    pending_order_expiry_bars: int = 1  # Number of bars a pending limit order stays active waiting to be filled before being discarded. Default 1 preserves prior behavior (check next bar only, then discard).


@dataclass(frozen=True)
class BacktestTrade:
    """Represents a simulated executed trade inside the backtester."""

    entry_time: datetime
    exit_time: datetime
    direction: SignalDirection
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    result: TradeResult
    pnl: float
    r_multiple: float
    symbol: str = ""
    setup_id: str = ""
    strategy_name: str = ""
    trigger_reason: str = ""
    confidence_score: float = 0.0
    bars_held: int = 0
    position_size: float = 0.0
    entry_bar_index: int = 0
    exit_bar_index: int = 0
    trade_duration: float = 0.0
    entry_spread: float = 0.0
    exit_spread: float = 0.0


@dataclass(frozen=True)
class BacktestResult:
    """Aggregates all execution outputs and performance statistics from a backtest run."""

    trades: tuple[BacktestTrade, ...]
    total_profit: float
    win_rate: float
    max_drawdown: float
    profit_factor: float
    initial_balance: float = 0.0
    final_balance: float = 0.0
    account_blown: bool = False
    blown_at_trade_index: int | None = None
    daily_loss_limit_hits: int = 0
    stopped_early: bool = False
    stop_reason: str | None = None

