"""Unit tests for SrDailyBiasStrategy and compute_daily_bias_context().

Uses tiny warmup periods (swing_len/atr_len/adx_len/vol_sma_len all small) so
test bar sequences stay short -- these are NOT the validated production
defaults (see SrDailyBiasConfig's own docstring for those), just enough
history to exercise each gate deterministically.
"""

from datetime import datetime, date
from zoneinfo import ZoneInfo

import pytest

from core.models import Bar, SignalDirection, Timeframe
from market_structure.structure_models import MarketState
from strategy.diagnostics import RejectionReason
from strategy.sr_daily_bias import (
    DailyBiasContext,
    SrDailyBiasConfig,
    SrDailyBiasStrategy,
    compute_daily_bias_context,
)

UTC = ZoneInfo("UTC")


def _bar(day: int, hour: int, minute: int, o: float, h: float, l: float, c: float, vol: float = 100.0, month: int = 1) -> Bar:
    return Bar(
        timestamp=datetime(2026, month, day, hour, minute, tzinfo=UTC),
        open=o, high=h, low=l, close=c, volume=vol, spread=0.0,
    )


def _new_state() -> MarketState:
    return MarketState(symbol="TEST", timeframe=Timeframe.M15)


def _tiny_config(**overrides) -> SrDailyBiasConfig:
    base = dict(
        swing_len=2, atr_len=2, adx_len=2, vol_sma_len=2, retest_max_bars=10,
        use_adx_filter=False, require_vol_on_bounce=False,
    )
    base.update(overrides)
    return SrDailyBiasConfig(**base)


class TestDailyBiasContext:
    def test_none_without_enough_warmup(self) -> None:
        daily = [Bar(timestamp=datetime(2026, 1, d, tzinfo=UTC), open=1, high=1, low=1, close=100.0, volume=1) for d in range(1, 5)]
        assert compute_daily_bias_context(daily, for_date=date(2026, 1, 6), ema_len=20) is None

    def test_bullish_when_close_clears_upper_band(self) -> None:
        daily = [Bar(timestamp=datetime(2026, 1, d, tzinfo=UTC), open=1, high=1, low=1, close=100.0, volume=1) for d in range(1, 22)]
        daily[-1] = Bar(timestamp=daily[-1].timestamp, open=1, high=1, low=1, close=200.0, volume=1)
        ctx = compute_daily_bias_context(daily, for_date=date(2026, 1, 22), ema_len=20, neutral_pct=0.15)
        assert ctx is not None
        assert ctx.bias == 1

    def test_neutral_when_close_near_ema(self) -> None:
        daily = [Bar(timestamp=datetime(2026, 1, d, tzinfo=UTC), open=1, high=1, low=1, close=100.0, volume=1) for d in range(1, 23)]
        ctx = compute_daily_bias_context(daily, for_date=date(2026, 1, 23), ema_len=20, neutral_pct=0.15)
        assert ctx is not None
        assert ctx.bias == 0

    def test_ignores_bars_on_or_after_for_date(self) -> None:
        daily = [Bar(timestamp=datetime(2026, 1, d, tzinfo=UTC), open=1, high=1, low=1, close=100.0, volume=1) for d in range(1, 22)]
        # A bar dated ON for_date with an extreme close must NOT leak into the EMA/last-close calc.
        daily.append(Bar(timestamp=datetime(2026, 1, 22, tzinfo=UTC), open=1, high=1, low=1, close=99999.0, volume=1))
        ctx = compute_daily_bias_context(daily, for_date=date(2026, 1, 22), ema_len=20, neutral_pct=0.15)
        assert ctx is not None
        assert ctx.bias == 0  # would be wildly bullish if the same-day bar leaked in


class TestBreakoutFreshness:
    """Regression test for the prev_close bug: self._prev_bar was being
    reassigned to the CURRENT bar inside _update_atr_adx() before the
    breakout-freshness check read it, making `fresh_up`/`fresh_down`
    permanently False. See strategy/sr_daily_bias.py's fix comment.
    """

    def test_bullish_breakout_fires_on_the_bar_that_first_clears_resistance(self) -> None:
        # fixed_rr bypasses the liquidity-zone TP lookup for this test -- that
        # mechanism (needing a FURTHER, still-unmitigated level beyond the one
        # just broken) is exercised by the validated backtest already; this
        # test only checks that the breakout itself fires correctly.
        strategy = SrDailyBiasStrategy(config=_tiny_config(min_sr_dist_atr=0.1, breakout_buffer_atr=0.0, min_risk_atr=0.01, max_risk_atr=100.0, min_reward_atr=0.01, fixed_rr=3.0))
        state = _new_state()
        strategy.set_daily_bias_context(DailyBiasContext(for_date=date(2026, 1, 10), bias=1))

        # Build a resistance (110) and support (90) via one pivot-forming bar, then a bar
        # that closes clearly above resistance with a volume spike -- must fire BULLISH
        # BREAKOUT. With swing_len=2, a pivot needs 2 bars strictly before AND after it.
        bars = [
            _bar(10, 0, 0, 100, 105, 95, 100),
            _bar(10, 0, 15, 100, 103, 97, 100),
            _bar(10, 0, 30, 100, 110, 90, 100),   # pivot-high (110) AND pivot-low (90) candidate
            _bar(10, 0, 45, 100, 103, 97, 100),
            _bar(10, 1, 0, 100, 105, 95, 100),    # confirms both pivots above
            _bar(10, 1, 15, 100, 101, 99, 100),
            _bar(10, 1, 30, 90, 130, 89, 125, vol=200.0),  # breaks above 110 with a volume spike -- BREAKOUT bar
        ]
        setup = None
        for b in bars:
            state.append_bar(b)
            setup = strategy.evaluate(state)

        assert setup is not None, f"expected a breakout setup, diagnostics={strategy.diagnostics.summary()}"
        assert setup.direction == SignalDirection.BUY
        assert "Breakout" in setup.trigger_reason


class TestRetestSlBase:
    """Regression test for the retest sl_base bug: broken_res_level/
    broken_sup_level were read AFTER being cleared to None by the "consume
    the broken level" step, making sl_base None (and crashing) for every
    Retest setup. See strategy/sr_daily_bias.py's fix comment.
    """

    def test_retest_setup_has_a_real_numeric_stop_loss(self) -> None:
        # touch_tolerance_atr is kept small and deliberately below the
        # small-config ATR*0.5 magnitude so the retest zone (around 110)
        # cannot also accidentally satisfy the FAR-away support-touch
        # condition (around 90) on the same bar -- Bounce takes priority
        # over Retest when both match (same tie-break as the validated
        # backtest), which would otherwise mask this test's intent.
        strategy = SrDailyBiasStrategy(config=_tiny_config(min_sr_dist_atr=0.1, breakout_buffer_atr=0.0, touch_tolerance_atr=0.5, min_risk_atr=0.01, max_risk_atr=100.0, min_reward_atr=0.01, fixed_rr=3.0))
        state = _new_state()
        strategy.set_daily_bias_context(DailyBiasContext(for_date=date(2026, 1, 10), bias=1))

        bars = [
            _bar(10, 0, 0, 100, 105, 95, 100),
            _bar(10, 0, 15, 100, 103, 97, 100),
            _bar(10, 0, 30, 100, 110, 90, 100),   # pivot-high (110) AND pivot-low (90) candidate
            _bar(10, 0, 45, 100, 103, 97, 100),
            _bar(10, 1, 0, 100, 105, 95, 100),    # confirms both pivots above
            _bar(10, 1, 15, 100, 101, 99, 100),
            _bar(10, 1, 30, 90, 130, 89, 125, vol=200.0),  # bullish breakout of ~110 resistance
        ]
        setup = None
        for b in bars:
            state.append_bar(b)
            setup = strategy.evaluate(state)
        assert setup is not None and "Breakout" in setup.trigger_reason

        # Now pull price back down to retest the broken level (~110) with a bullish rejection bar.
        retest_bars = [
            _bar(10, 1, 45, 125, 126, 120, 121),
            _bar(10, 2, 0, 121, 122, 115, 116),
            _bar(10, 2, 15, 116, 117, 108, 115),  # wicks down toward 110ish, closes back up -> rejection
        ]
        setup = None
        for b in retest_bars:
            state.append_bar(b)
            setup = strategy.evaluate(state)

        assert setup is not None and "Retest" in setup.trigger_reason, f"expected a retest setup, diagnostics={strategy.diagnostics.summary()}"
        assert isinstance(setup.stop_zone[0], float)
        assert setup.stop_zone[0] < setup.entry_zone[0]  # LONG: SL below entry


class TestBiasGating:
    def test_neutral_bias_rejects_every_bar(self) -> None:
        strategy = SrDailyBiasStrategy(config=_tiny_config())
        state = _new_state()
        strategy.set_daily_bias_context(DailyBiasContext(for_date=date(2026, 1, 10), bias=0))
        setup = None
        for b in [_bar(10, 0, m, 100, 101, 99, 100) for m in range(0, 60, 15)]:
            state.append_bar(b)
            setup = strategy.evaluate(state)
        assert setup is None
        assert strategy.diagnostics.rejections[RejectionReason.NEUTRAL_BIAS] > 0

    def test_no_context_rejects_with_no_daily_bias_yet(self) -> None:
        strategy = SrDailyBiasStrategy(config=_tiny_config())
        state = _new_state()
        state.append_bar(_bar(10, 0, 0, 100, 101, 99, 100))
        setup = strategy.evaluate(state)
        assert setup is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_DAILY_BIAS_YET] == 1

    def test_stale_context_for_a_different_date_is_treated_as_no_context(self) -> None:
        strategy = SrDailyBiasStrategy(config=_tiny_config())
        state = _new_state()
        strategy.set_daily_bias_context(DailyBiasContext(for_date=date(2026, 1, 9), bias=1))  # yesterday's context
        state.append_bar(_bar(10, 0, 0, 100, 101, 99, 100))  # bar is on the 10th
        setup = strategy.evaluate(state)
        assert setup is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_DAILY_BIAS_YET] == 1


class TestResetAndInterfaceCompliance:
    def test_reset_clears_diagnostics_and_context(self) -> None:
        strategy = SrDailyBiasStrategy(config=_tiny_config())
        strategy.set_daily_bias_context(DailyBiasContext(for_date=date(2026, 1, 10), bias=1))
        state = _new_state()
        state.append_bar(_bar(10, 0, 0, 100, 101, 99, 100))
        strategy.evaluate(state)
        assert strategy.diagnostics.evaluations == 1

        strategy.reset()
        assert strategy.diagnostics.evaluations == 0
        assert strategy._daily_bias_context is None

    def test_recommended_max_holding_bars_is_none(self) -> None:
        strategy = SrDailyBiasStrategy()
        assert strategy.recommended_max_holding_bars(Timeframe.M15) is None
