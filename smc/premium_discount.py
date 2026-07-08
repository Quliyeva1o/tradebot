"""Premium / Discount Zone calculation logic."""

from dataclasses import dataclass
from enum import Enum


class ZoneType(Enum):
    """Supported Premium/Discount zone classifications."""

    PREMIUM = "premium"
    DISCOUNT = "discount"
    EQUILIBRIUM = "equilibrium"


@dataclass(frozen=True)
class PremiumDiscountZone:
    """Represents a calculated Premium/Discount zone configuration."""

    high: float
    low: float
    equilibrium: float
    current_price: float
    zone: ZoneType


class PremiumDiscountCalculator:
    """Calculates Premium/Discount zones from swing highs/lows and current price."""

    @staticmethod
    def calculate(high: float, low: float, current_price: float) -> PremiumDiscountZone:
        """Calculates the Premium/Discount zone.

        Args:
            high: Swing high price.
            low: Swing low price.
            current_price: The price to evaluate against equilibrium.

        Returns:
            The computed PremiumDiscountZone.

        Raises:
            ValueError: If high is less than or equal to low.
        """
        if high <= low:
            raise ValueError(
                f"Invalid high/low values: high ({high}) must be greater than low ({low})."
            )

        equilibrium = (high + low) / 2.0

        if current_price > equilibrium:
            zone = ZoneType.PREMIUM
        elif current_price < equilibrium:
            zone = ZoneType.DISCOUNT
        else:
            zone = ZoneType.EQUILIBRIUM

        return PremiumDiscountZone(
            high=high,
            low=low,
            equilibrium=equilibrium,
            current_price=current_price,
            zone=zone,
        )
