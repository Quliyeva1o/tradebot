"""Risk-based position sizing using the venue's own contract-size/tick-value math.

Converts a risk percentage + stop distance into a real lot size. Mirrors
backtest.engine.SimplePositionSizer's risk_amount / stop_distance shape, but
sizes in real lots (via SymbolConstraints.tick_size/tick_value) rather than
backtest's simplified price-unit formula -- see execution/interfaces.py's
IBroker.get_symbol_constraints().
"""

import math
from dataclasses import dataclass

from config.settings import Settings
from core.models import SymbolConstraints
from core.validation import require_positive


@dataclass(frozen=True)
class SizingOutcome:
    """What the last calculate_size() call actually did.

    Exists because the volume_min clamp is silent and can only ever raise risk.
    When the risk budget buys less than one minimum lot there is no way to
    honour it -- the venue has no smaller size -- so the sizer returns the
    minimum and the trade risks more than asked. Nothing recorded that.

    Measured live 2026-09-09: NDX100 on a $5,008 account at 0.5% wanted 0.0073
    lots against a 172.5-point stop; the 0.01 minimum made the real risk $34.50,
    or 0.688%. Across the deployed symbols only NDX100 is affected (median
    min-lot risk 0.829% against a 0.5% target); the others sit at 0.004-0.408%.
    On a prop account with a daily-loss limit, an unintended 1.7x on one symbol
    is exactly what breaches it, so it needs to be visible rather than inferred
    afterwards from the P&L.

    Attributes:
        volume: The lot size returned.
        wanted_volume: The unrounded size the risk budget actually bought.
        risk_amount: The intended risk in account currency.
        actual_risk: What `volume` really risks if the stop is hit.
        clamped_to_min: True when volume_min raised the size above the budget.
    """

    volume: float
    wanted_volume: float
    risk_amount: float
    actual_risk: float
    clamped_to_min: bool

    @property
    def risk_multiple(self) -> float:
        """actual_risk / risk_amount -- 1.0 when the budget was honoured."""
        return self.actual_risk / self.risk_amount if self.risk_amount > 0 else 1.0


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
        # Set by every calculate_size() call; read by the live runners so a
        # clamped (over-budget) size is logged instead of passing silently.
        self.last_sizing: SizingOutcome | None = None

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
            self.last_sizing = None
            return 0.0

        risk_amount = balance * self.risk_per_trade_pct
        distance_ticks = distance_price / constraints.tick_size
        loss_per_lot = distance_ticks * constraints.tick_value
        if loss_per_lot == 0.0:
            self.last_sizing = None
            return 0.0

        raw_volume = risk_amount / loss_per_lot
        stepped_volume = math.floor(raw_volume / constraints.volume_step) * constraints.volume_step
        volume = max(constraints.volume_min, min(stepped_volume, constraints.volume_max))
        self.last_sizing = SizingOutcome(
            volume=volume,
            wanted_volume=raw_volume,
            risk_amount=risk_amount,
            actual_risk=volume * loss_per_lot,
            # Only the LOW clamp is a risk problem. volume_max clamps downward,
            # which under-risks -- undesirable but never dangerous.
            clamped_to_min=volume > stepped_volume,
        )
        return volume
