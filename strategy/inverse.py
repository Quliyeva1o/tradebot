"""The exact opposite of a setup: sell where it buys, with its stop and target swapped.

For the inverse paper bots (--inverse, asked for 2026-09-17 after a month in which 50 of 58 Demo
trades stopped out). The mirrored trade's target is the original's stop and its stop is the
original's target, so every trade the original loses at its stop, the mirror wins at its target,
and every original target is a mirror stop. Spread is paid on both, so the mirror's PF is below
1 / the original's.

Sizing follows the mirror's own stop: against a 4R original that stop is four times wider, so at
the same risk percentage the mirror trades a quarter of the lots and wins 0.25R when the original
loses 1R.

Live replay of the six inverse paper bots before deploying (data to 2026-09-17, pooled): PF 0.76
(-190.8R) over 6.7 years, 0.81 (-26.8R) over the last 12 months, 1.77 (+6.2R, 57 trades) over
the last month. The mirror wins only while its original is losing.
"""

from dataclasses import replace

from core.models import SignalDirection
from strategy.models import TradeSetup

INVERSE_SUFFIX = "_inv"


def mirror_setup(setup: TradeSetup) -> TradeSetup:
    """`setup` turned around: opposite direction, stop and target zones swapped, same entry."""
    opposite = SignalDirection.SELL if setup.direction == SignalDirection.BUY else SignalDirection.BUY
    return replace(
        setup,
        setup_id=f"{setup.setup_id}{INVERSE_SUFFIX}",
        direction=opposite,
        stop_zone=setup.target_zone,
        target_zone=setup.stop_zone,
        trigger_reason=f"inverse of: {setup.trigger_reason}",
    )
