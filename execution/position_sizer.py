"""Risk-based position sizing using the venue's own contract-size/tick-value math.

Converts a risk percentage + stop distance into a real lot size. Mirrors
backtest.engine.SimplePositionSizer's risk_amount / stop_distance shape, but
sizes in real lots (via SymbolConstraints.tick_size/tick_value) rather than
backtest's simplified price-unit formula -- see execution/interfaces.py's
IBroker.get_symbol_constraints().
"""

import math

from config.settings import Settings
from core.models import SymbolConstraints
from core.validation import require_positive


class PositionSizer:
    """Computes a lot size from account balance, risk %, stop distance, and symbol constraints."""

    def __init__(self, risk_per_trade_pct: float | None = None) -> None:
        """Initializes the PositionSizer.

        Args:
            risk_per_trade_pct: Fraction of account balance to risk per
                trade (e.g. 0.01 = 1%). Defaults to Settings.load().RISK_PER_TRADE_PCT,
                the same default-from-Settings pattern as
                risk.daily_risk_tracker.DailyRiskTracker.max_daily_loss_pct.

        Raises:
            ValueError: If risk_per_trade_pct is not strictly positive.
        """
        self.risk_per_trade_pct = (
            risk_per_trade_pct if risk_per_trade_pct is not None else Settings.load().RISK_PER_TRADE_PCT
        )
        require_positive(self.risk_per_trade_pct, "risk_per_trade_pct")

    def calculate_size(
        self,
        balance: float,
        entry_price: float,
        stop_loss: float,
        constraints: SymbolConstraints,
    ) -> float:
        """Computes the lot size that risks risk_per_trade_pct of balance.

        Args:
            balance: Account balance to size against.
            entry_price: Intended entry price (see strategy.risk_reward.resolve_entry_price).
            stop_loss: Intended stop-loss price (see execution.stop_engine.StopEngine).
            constraints: The traded symbol's SymbolConstraints (contract
                size/tick size/tick value/volume min/max/step).

        Returns:
            A lot size rounded down to constraints.volume_step and clamped
            to [volume_min, volume_max]. 0.0 if entry_price == stop_loss
            (zero risk distance -- sizing is undefined, not a divide-by-zero
            error).

        Raises:
            ValueError: If balance is not strictly positive.
        """
        require_positive(balance, "balance")

        distance_price = abs(entry_price - stop_loss)
        if distance_price == 0.0:
            return 0.0

        risk_amount = balance * self.risk_per_trade_pct
        distance_ticks = distance_price / constraints.tick_size
        loss_per_lot = distance_ticks * constraints.tick_value
        if loss_per_lot == 0.0:
            return 0.0

        raw_volume = risk_amount / loss_per_lot
        stepped_volume = math.floor(raw_volume / constraints.volume_step) * constraints.volume_step
        return max(constraints.volume_min, min(stepped_volume, constraints.volume_max))
