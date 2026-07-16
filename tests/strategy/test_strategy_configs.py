"""Regression tests for strategy config __post_init__ validation.

Every strategy config dataclass now validates its numeric fields at
construction time (core/validation.py: require_positive/require_non_negative)
instead of letting an invalid value (e.g. risk_reward=-1) silently propagate
into evaluate() as a late RejectionReason.RR_GATE_FAILED / NON_POSITIVE_RISK,
or worse, a ZeroDivisionError deep in a moving-average window.

Two guarantees are under test for every config:
1. NO BEHAVIOR CHANGE: existing, in-range default/typical values still
   construct without error, whether via the Config object or via the
   strategy's direct kwargs, and produce identical attribute values.
2. Out-of-range values now raise ValueError immediately, via EITHER calling
   convention (Config object or direct strategy kwargs), since the strategy
   constructors were refactored to always build/validate a Config
   internally.

Also covers the isinstance guard added to every strategy's __init__: passing
a config object of the wrong type raises TypeError.
"""

from datetime import time
from typing import Any

import pytest

from strategy.accumulation_breakout import (
    AccumulationBreakoutConfig,
    AccumulationBreakoutStrategy,
)
from strategy.continuation import (
    BearishContinuationStrategy,
    BullishContinuationStrategy,
    StrategyConfig,
)
from strategy.manipulation_reversal import (
    ManipulationReversalConfig,
    ManipulationReversalStrategy,
)
from strategy.nasdaq_midline_sweep import NasdaqMidlineSweepConfig, NasdaqMidlineSweepStrategy
from strategy.opening_range_breakout import (
    OpeningRangeBreakoutConfig,
    OpeningRangeBreakoutStrategy,
)
from strategy.order_block_retest import OrderBlockRetestConfig, OrderBlockRetestStrategy


class TestAccumulationBreakoutConfigValidation:
    def test_default_config_unchanged(self) -> None:
        strategy = AccumulationBreakoutStrategy(config=AccumulationBreakoutConfig())
        assert strategy.risk_reward == 2.0
        assert strategy.volume_multiplier == 1.5
        assert strategy.body_multiplier == 1.3
        assert strategy.accumulation_bars == 5
        assert strategy.sma_period == 20

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("risk_reward", 0.0),
            ("risk_reward", -1.0),
            ("volume_multiplier", 0.0),
            ("volume_multiplier", -1.5),
            ("body_multiplier", 0.0),
            ("accumulation_bars", 0),
            ("accumulation_bars", -5),
            ("sma_period", 0),
        ],
    )
    def test_config_object_rejects_invalid_field(self, field: str, bad_value: Any) -> None:
        with pytest.raises(ValueError, match=field):
            AccumulationBreakoutConfig(**{field: bad_value})

    def test_direct_kwarg_construction_also_validates(self) -> None:
        """The common (non-config=) calling convention must validate too."""
        with pytest.raises(ValueError, match="risk_reward"):
            AccumulationBreakoutStrategy(risk_reward=-1.0)

    def test_session_start_equal_to_session_end_raises(self) -> None:
        """Bug #60: session_start must be strictly before session_end -- equal
        values would make is_in_session() silently never match, permanently
        zero-trading the strategy with no error at construction time.
        """
        with pytest.raises(ValueError, match="session_start"):
            AccumulationBreakoutConfig(session_start=time(9, 30), session_end=time(9, 30))

    def test_session_start_after_session_end_raises(self) -> None:
        """Bug #60: a swapped/typo'd pair (e.g. hours out of order in .env)."""
        with pytest.raises(ValueError, match="session_start"):
            AccumulationBreakoutConfig(session_start=time(11, 0), session_end=time(9, 30))

    def test_session_start_before_session_end_still_constructs(self) -> None:
        """Differential: a valid, non-default in-order pair must still work."""
        config = AccumulationBreakoutConfig(session_start=time(8, 0), session_end=time(9, 0))
        assert config.session_start == time(8, 0)
        assert config.session_end == time(9, 0)

    def test_wrong_config_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            AccumulationBreakoutStrategy(config=OrderBlockRetestConfig())  # type: ignore[arg-type]


class TestNasdaqMidlineSweepConfigValidation:
    def test_default_config_unchanged(self) -> None:
        strategy = NasdaqMidlineSweepStrategy(config=NasdaqMidlineSweepConfig())
        assert strategy.range_size == 10.0
        assert strategy.risk_reward == 2.0
        assert strategy.mid_buffer == 5.0
        assert strategy.body_multiplier == 1.5
        assert strategy.sma_period == 20

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("range_size", 0.0),
            ("range_size", -10.0),
            ("risk_reward", 0.0),
            ("mid_buffer", -0.01),
            ("body_multiplier", 0.0),
            ("sma_period", 0),
        ],
    )
    def test_config_object_rejects_invalid_field(self, field: str, bad_value: Any) -> None:
        with pytest.raises(ValueError, match=field):
            NasdaqMidlineSweepConfig(**{field: bad_value})

    def test_mid_buffer_zero_is_valid_boundary(self) -> None:
        """mid_buffer is non-negative (not strictly positive) -- 0.0 is a
        legitimate 'no buffer' configuration and must still construct."""
        config = NasdaqMidlineSweepConfig(mid_buffer=0.0)
        assert config.mid_buffer == 0.0

    def test_direct_kwarg_construction_also_validates(self) -> None:
        with pytest.raises(ValueError, match="risk_reward"):
            NasdaqMidlineSweepStrategy(risk_reward=-2.0)

    def test_wrong_config_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            NasdaqMidlineSweepStrategy(config=OrderBlockRetestConfig())  # type: ignore[arg-type]

    def test_build_session_start_equal_to_end_raises(self) -> None:
        """Bug #60: build_session_start must be strictly before
        build_session_end -- equal values would make the midline never
        freeze, permanently zero-trading the strategy with no error at
        construction time.
        """
        with pytest.raises(ValueError, match="build_session_start"):
            NasdaqMidlineSweepConfig(build_session_start=time(9, 30), build_session_end=time(9, 30))

    def test_build_session_start_after_end_raises(self) -> None:
        with pytest.raises(ValueError, match="build_session_start"):
            NasdaqMidlineSweepConfig(build_session_start=time(9, 50), build_session_end=time(9, 30))

    def test_build_session_start_before_end_still_constructs(self) -> None:
        """Differential: the validated USTEC default pair (09:30-09:50) --
        and any other valid, in-order pair -- must still construct.
        """
        config = NasdaqMidlineSweepConfig()
        assert config.build_session_start == time(9, 30)
        assert config.build_session_end == time(9, 50)

        custom = NasdaqMidlineSweepConfig(build_session_start=time(8, 0), build_session_end=time(8, 20))
        assert custom.build_session_start == time(8, 0)
        assert custom.build_session_end == time(8, 20)


class TestOpeningRangeBreakoutConfigValidation:
    def test_default_config_unchanged(self) -> None:
        strategy = OpeningRangeBreakoutStrategy(config=OpeningRangeBreakoutConfig())
        assert strategy.volume_multiplier == 1.5
        assert strategy.volume_lookback == 20
        assert strategy.risk_reward == 3.0
        assert strategy.range_bars == 5

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("volume_multiplier", 0.0),
            ("volume_lookback", 0),
            ("risk_reward", -3.0),
            ("range_bars", 0),
        ],
    )
    def test_config_object_rejects_invalid_field(self, field: str, bad_value: Any) -> None:
        with pytest.raises(ValueError, match=field):
            OpeningRangeBreakoutConfig(**{field: bad_value})

    def test_direct_kwarg_construction_also_validates(self) -> None:
        with pytest.raises(ValueError, match="range_bars"):
            OpeningRangeBreakoutStrategy(range_bars=0)

    def test_wrong_config_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            OpeningRangeBreakoutStrategy(config=OrderBlockRetestConfig())  # type: ignore[arg-type]


class TestManipulationReversalConfigValidation:
    def test_default_config_unchanged(self) -> None:
        strategy = ManipulationReversalStrategy(config=ManipulationReversalConfig())
        assert strategy.tp_extension_bars == 3
        assert strategy.tp_extension_multiple == 2.0

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("tp_extension_bars", 0),
            ("tp_extension_bars", -3),
            ("tp_extension_multiple", 0.0),
            ("tp_extension_multiple", -2.0),
        ],
    )
    def test_config_object_rejects_invalid_field(self, field: str, bad_value: Any) -> None:
        with pytest.raises(ValueError, match=field):
            ManipulationReversalConfig(**{field: bad_value})

    def test_direct_kwarg_construction_also_validates(self) -> None:
        with pytest.raises(ValueError, match="tp_extension_multiple"):
            ManipulationReversalStrategy(tp_extension_multiple=0.0)

    def test_wrong_config_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            ManipulationReversalStrategy(config=OrderBlockRetestConfig())  # type: ignore[arg-type]


class TestOrderBlockRetestConfigValidation:
    def test_default_config_unchanged(self) -> None:
        strategy = OrderBlockRetestStrategy(config=OrderBlockRetestConfig())
        assert strategy.risk_reward == 2.0

    @pytest.mark.parametrize("bad_value", [0.0, -2.0])
    def test_config_object_rejects_invalid_risk_reward(self, bad_value: float) -> None:
        with pytest.raises(ValueError, match="risk_reward"):
            OrderBlockRetestConfig(risk_reward=bad_value)

    def test_direct_kwarg_construction_also_validates(self) -> None:
        with pytest.raises(ValueError, match="risk_reward"):
            OrderBlockRetestStrategy(risk_reward=-1.0)

    def test_wrong_config_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            OrderBlockRetestStrategy(config=AccumulationBreakoutConfig())  # type: ignore[arg-type]


class TestContinuationStrategyConfigValidation:
    """Shared StrategyConfig, used by both Bullish/BearishContinuationStrategy."""

    def test_default_config_unchanged(self) -> None:
        for cls in (BullishContinuationStrategy, BearishContinuationStrategy):
            strategy = cls(config=StrategyConfig())
            assert strategy.pip_size == 0.0001
            assert strategy.lookback_bars == 20
            assert strategy.fvg_proximity_pips == 50.0
            assert strategy.stop_buffer_pips == 5.0
            assert strategy.min_risk_reward_ratio == 1.0
            assert strategy.max_break_age_bars is None

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("pip_size", 0.0),
            ("pip_size", -0.0001),
            ("lookback_bars", 0),
            ("fvg_proximity_pips", -1.0),
            ("min_risk_reward_ratio", -1.0),
            ("max_break_age_bars", 0),
            ("max_break_age_bars", -5),
        ],
    )
    def test_config_object_rejects_invalid_field(self, field: str, bad_value: Any) -> None:
        with pytest.raises(ValueError, match=field):
            StrategyConfig(**{field: bad_value})

    def test_stop_buffer_pips_negative_still_allowed(self) -> None:
        """stop_buffer_pips is intentionally left unvalidated: existing tests
        (test_strategy_diagnostics.py, test_strategy_engine.py) construct
        BullishContinuationStrategy(stop_buffer_pips=-50.0) on purpose, to
        force a NON_POSITIVE_RISK rejection at evaluate()-time. Adding a
        non-negative constraint here would be a real behavior change, not
        just a validation of previously-impossible input -- so it's
        deliberately excluded from StrategyConfig.__post_init__.
        """
        config = StrategyConfig(stop_buffer_pips=-50.0)
        assert config.stop_buffer_pips == -50.0
        strategy = BullishContinuationStrategy(stop_buffer_pips=-50.0)
        assert strategy.stop_buffer_pips == -50.0

    def test_max_break_age_bars_none_still_allowed(self) -> None:
        """None disables the stale-break gate (existing default behavior)."""
        config = StrategyConfig(max_break_age_bars=None)
        assert config.max_break_age_bars is None

    def test_direct_kwarg_construction_also_validates(self) -> None:
        with pytest.raises(ValueError, match="lookback_bars"):
            BullishContinuationStrategy(lookback_bars=0)
        with pytest.raises(ValueError, match="lookback_bars"):
            BearishContinuationStrategy(lookback_bars=0)

    def test_wrong_config_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            BullishContinuationStrategy(config=OrderBlockRetestConfig())  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            BearishContinuationStrategy(config=OrderBlockRetestConfig())  # type: ignore[arg-type]
