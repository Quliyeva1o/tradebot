"""Guards added by the 2026-08-27 production-readiness audit.

Two independent failure modes, both measured against real history before
being fixed:

1. MARGIN CEILING -- risk-based sizing divides the risk budget by the stop
   distance, so an unusually tight stop yields an enormous lot size. On real
   data SR+Bias NAS100 30m produced 11 entries whose margin alone exceeded
   100% of a $100k account (worst: 66.3 lots needing $207,783 = 208%), and
   one XAUUSD entry needed 89% of free margin. BacktestEngine has always had
   _margin_ok(); the live path had no equivalent.

2. POSITION OWNERSHIP -- several bots share one account and two of them
   trade NAS100 (First FVG on M1, SR+Bias on M30). Filtering open positions
   by symbol alone made them indistinguishable, so each bot would manage --
   and could close -- the other's position, and two simultaneous positions
   made BOTH bail out as "ambiguous", leaving neither managed.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

import run_live_midnight_fvg
import run_live_sr_bias
from core.models import AccountInfo, OrderType, SignalDirection, SymbolConstraints, Timeframe
from execution.models import OrderResult, Position
from execution.position_sizer import PositionSizer
from execution.trade_manager import DEFAULT_MAX_MARGIN_PCT, TradeManager
from strategy.models import TradeSetup

UTC = ZoneInfo("UTC")


def _setup(symbol: str = "NAS100") -> TradeSetup:
    return TradeSetup(
        setup_id=f"setup_test_{symbol}", symbol=symbol, timeframe=Timeframe.M1,
        direction=SignalDirection.BUY, entry_zone=(100.0, 100.0), stop_zone=(99.0, 99.0),
        target_zone=(103.0, 103.0), confidence_score=1.0, confluence=[], trigger_reason="t",
        invalidations=[], related_structure_break=None, related_order_block=None,
        related_fvg=None, timestamp=datetime(2026, 1, 10, tzinfo=UTC),
    )


def _broker(margin: float | None, equity: float = 100_000.0) -> Mock:
    broker = Mock()
    broker.get_account_info.return_value = AccountInfo(
        balance=equity, equity=equity, margin=0.0, free_margin=equity, leverage=100
    )
    broker.get_symbol_constraints.return_value = SymbolConstraints(
        symbol="NAS100", contract_size=1.0, tick_size=0.1, tick_value=0.1,
        volume_min=0.01, volume_max=300.0, volume_step=0.01,
    )
    broker.calculate_margin.return_value = margin
    broker.place_order.return_value = OrderResult(
        success=True, order_id="1", position_id="p1", price=100.0, volume=1.0
    )
    return broker


def _submitted_volume(broker: Mock) -> float:
    return broker.place_order.call_args[0][0].volume


class TestMarginCeiling:
    def test_oversized_entry_is_scaled_down_not_dropped(self) -> None:
        # 50% of equity, well past the 20% default ceiling -> expect the
        # trade to still happen, at 20/50 of the requested size.
        broker = _broker(margin=50_000.0)
        tm = TradeManager(volume=1.0)

        tm.open_trade(_setup(), broker)

        broker.place_order.assert_called_once(), "scaling must keep the trade, not drop it"
        assert _submitted_volume(broker) == pytest.approx(0.4, abs=0.01)
        assert tm.has_open_trade is True

    def test_entry_within_the_ceiling_is_left_untouched(self) -> None:
        broker = _broker(margin=5_000.0)  # 5% of equity
        tm = TradeManager(volume=1.0)

        tm.open_trade(_setup(), broker)

        assert _submitted_volume(broker) == 1.0
        assert tm.has_open_trade is True

    def test_the_real_worst_case_from_history_is_scaled_into_the_ceiling(self) -> None:
        """Measured worst case at this account's real contract sizes:
        SR+Bias XAUUSD, 47 lots needing $90,522 margin on $100k equity.
        """
        broker = _broker(margin=90_522.0)
        tm = TradeManager(volume=47.0)

        tm.open_trade(_setup("XAUUSD"), broker)

        submitted = _submitted_volume(broker)
        implied_margin = 90_522.0 * (submitted / 47.0)
        assert implied_margin <= 100_000.0 * DEFAULT_MAX_MARGIN_PCT + 1e-6
        assert submitted > 0, "a tradeable size must remain"

    def test_entry_is_refused_when_even_the_minimum_lot_breaches_the_ceiling(self) -> None:
        broker = _broker(margin=5_000_000.0)  # absurd: min lot alone blows the ceiling
        tm = TradeManager(volume=1.0)

        tm.open_trade(_setup(), broker)

        broker.place_order.assert_not_called()
        assert tm.has_open_trade is False
        assert tm.last_open_result is not None and tm.last_open_result.success is False
        assert "margin" in tm.last_open_result.comment.lower()

    def test_check_fails_open_when_the_venue_cannot_price_margin(self) -> None:
        """Blocking every trade on missing metadata would be worse than the
        risk it guards against -- proceed, but only because margin is None.
        """
        broker = _broker(margin=None)
        tm = TradeManager(volume=1.0)

        tm.open_trade(_setup(), broker)

        broker.place_order.assert_called_once()
        assert _submitted_volume(broker) == 1.0

    def test_ceiling_can_be_disabled_explicitly(self) -> None:
        broker = _broker(margin=99_000.0)
        tm = TradeManager(volume=1.0, max_margin_pct=None)

        tm.open_trade(_setup(), broker)

        assert _submitted_volume(broker) == 1.0

    def test_default_ceiling_is_a_fraction_not_a_percentage_number(self) -> None:
        """Guards against a 20-vs-0.20 unit slip silently disabling the gate."""
        assert 0 < DEFAULT_MAX_MARGIN_PCT < 1

    def test_capped_volume_respects_the_venue_volume_step(self) -> None:
        broker = _broker(margin=33_333.0)  # forces an untidy scale factor
        tm = TradeManager(volume=1.0)

        tm.open_trade(_setup(), broker)

        submitted = _submitted_volume(broker)
        assert round(submitted / 0.01) == pytest.approx(submitted / 0.01, abs=1e-6), (
            f"{submitted} is not a whole multiple of the 0.01 volume step"
        )

    def test_risk_sizing_and_margin_gate_compose(self) -> None:
        """With a sizer attached, the gate must price the SIZED volume."""
        broker = _broker(margin=80_000.0)
        tm = TradeManager(volume=0.1, position_sizer=PositionSizer(risk_per_trade_pct=0.002))

        tm.open_trade(_setup(), broker)

        broker.calculate_margin.assert_called_once()
        _sym, _otype, sized_volume, _price = broker.calculate_margin.call_args[0]
        assert sized_volume > 0
        assert _submitted_volume(broker) < sized_volume, "the sized volume should have been capped"


def _pos(symbol: str, comment: str, pos_id: str = "p1") -> Position:
    return Position(
        id=pos_id, symbol=symbol, order_type=OrderType.BUY_MARKET, volume=0.1,
        open_price=100.0, current_price=100.0, stop_loss=95.0, take_profit=110.0,
        comment=comment,
    )


class TestPositionOwnership:
    """Both live runners must recognise only their OWN positions."""

    @pytest.mark.parametrize(
        "module, own_comment, foreign_comment",
        [
            (run_live_midnight_fvg, "setup_midnight_fvg_NAS100_M1_BUY", "setup_sr_bias_NAS100_M30_BUY"),
            (run_live_sr_bias, "setup_sr_bias_NAS100_M30_BUY", "setup_midnight_fvg_NAS100_M1_BUY"),
        ],
        ids=["midnight_fvg", "sr_bias"],
    )
    def test_other_strategys_position_is_not_claimed(self, module, own_comment, foreign_comment) -> None:
        positions = [_pos("NAS100", own_comment, "mine"), _pos("NAS100", foreign_comment, "theirs")]

        mine, foreign = module._partition_positions(positions, "NAS100")

        assert [p.id for p in mine] == ["mine"]
        assert [p.id for p in foreign] == ["theirs"]

    @pytest.mark.parametrize("module", [run_live_midnight_fvg, run_live_sr_bias], ids=["midnight_fvg", "sr_bias"])
    def test_unknown_ownership_is_never_treated_as_ours(self, module) -> None:
        """A hand-opened position (no comment) must not be closable by a bot."""
        positions = [_pos("NAS100", "", "manual"), _pos("NAS100", "something_else", "other")]

        mine, foreign = module._partition_positions(positions, "NAS100")

        assert mine == []
        assert len(foreign) == 2

    @pytest.mark.parametrize("module", [run_live_midnight_fvg, run_live_sr_bias], ids=["midnight_fvg", "sr_bias"])
    def test_other_symbols_are_ignored_entirely(self, module) -> None:
        positions = [_pos("XAUUSD", module.STRATEGY_TAG + "_XAUUSD", "gold")]

        mine, foreign = module._partition_positions(positions, "NAS100")

        assert mine == [] and foreign == []

    def test_truncated_broker_comment_still_resolves_ownership(self) -> None:
        """MT5 caps comments at 29 chars (keeping the first 20 + a hash), so
        ownership must survive that truncation -- see mt5_broker._mt5_comment.
        """
        from execution.mt5_broker import _mt5_comment

        long_id = "setup_midnight_fvg_NAS100_M1_BUY_a1b2c3d4_20260110_000000_000000"
        truncated = _mt5_comment(long_id)

        assert len(truncated) <= 29
        mine, _ = run_live_midnight_fvg._partition_positions([_pos("NAS100", truncated)], "NAS100")
        assert len(mine) == 1, f"ownership lost after truncation to {truncated!r}"
