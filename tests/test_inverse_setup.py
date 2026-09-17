"""--inverse trades the exact opposite of a setup: its stop is the original's target and back."""

from datetime import UTC, datetime

from core.models import SignalDirection, Timeframe
from strategy.inverse import INVERSE_SUFFIX, mirror_setup
from strategy.models import TradeSetup
from strategy.risk_reward import resolve_entry_price, resolve_stop_and_target


def _setup(direction: SignalDirection = SignalDirection.BUY) -> TradeSetup:
    entry, stop, target = (4354.6, 4340.31, 4411.76) if direction == SignalDirection.BUY else (
        25391.66, 25482.74, 25209.5)
    return TradeSetup(
        setup_id="setup_nasdaq_orb_m1_XAUUSD_M1_BUY_20260916_150000", symbol="XAUUSD",
        timeframe=Timeframe.M1, direction=direction, entry_zone=(entry, entry),
        stop_zone=(stop, stop), target_zone=(target, target), confidence_score=1.0, confluence=[],
        trigger_reason="close above the opening range", invalidations=[],
        related_structure_break=None, related_order_block=None, related_fvg=None,
        timestamp=datetime(2026, 9, 16, 15, 0, tzinfo=UTC))


def test_a_buy_becomes_a_sell_with_stop_and_target_swapped() -> None:
    original = _setup()
    mirror = mirror_setup(original)
    assert mirror.direction == SignalDirection.SELL
    assert resolve_stop_and_target(mirror) == (4411.76, 4340.31)
    assert resolve_entry_price(mirror) == resolve_entry_price(original)


def test_a_sell_becomes_a_buy() -> None:
    mirror = mirror_setup(_setup(SignalDirection.SELL))
    assert mirror.direction == SignalDirection.BUY
    assert resolve_stop_and_target(mirror) == (25209.5, 25482.74)


def test_the_mirror_keeps_the_bots_tag_and_its_own_ledger_id() -> None:
    original = _setup()
    mirror = mirror_setup(original)
    assert mirror.setup_id == original.setup_id + INVERSE_SUFFIX
    assert mirror.setup_id.startswith("setup_nasdaq_orb_m1")
    assert mirror_setup(mirror).direction == original.direction
