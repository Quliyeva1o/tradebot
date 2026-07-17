"""Unit tests for research/pattern_discovery.py.

Covers: candidate generation (count + no contradictory FVG_EDGE cells), the
hand-rolled normal-CDF/z-test/Benjamini-Hochberg FDR math (no scipy/
statsmodels available in this environment), the minimum-sample-size and
positive-mean-R exclusion rules feeding into apply_fdr_phase, and a
differential test proving screen_candidates() (the shared-pipeline runner)
produces IDENTICAL trades to BacktestEngine.run() for one candidate.
"""

import math
from pathlib import Path

import pytest

from application.services.market_state_builder import MarketStateBuilder
from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from core.models import Timeframe
from data.csv_provider import CSVDataProvider
from smc.order_block import OBDirection
from strategy.parametrized_smc import EntryPoint, ParametrizedSMCStrategy, PatternCandidateConfig, TrendFilterMode
from strategy.strategy_engine import StrategyEngine

from research.pattern_discovery import (
    MIN_SAMPLE_SIZE,
    ScreeningRecord,
    _standard_normal_cdf,
    apply_fdr_phase,
    benjamini_hochberg,
    generate_candidates,
    one_sample_z_test,
    screen_candidates,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _dummy_config(**overrides) -> PatternCandidateConfig:
    defaults = dict(
        candidate_id="dummy",
        ob_direction=OBDirection.BULLISH,
        require_fvg=False,
        require_liquidity_sweep=False,
        entry_point=EntryPoint.OB_EDGE,
        take_profit_r=2.0,
        trend_filter=TrendFilterMode.NONE,
    )
    defaults.update(overrides)
    return PatternCandidateConfig(**defaults)


class TestGenerateCandidates:
    def test_total_count_is_240(self) -> None:
        candidates = generate_candidates()
        assert len(candidates) == 240

    def test_all_candidate_ids_unique(self) -> None:
        candidates = generate_candidates()
        ids = [c.candidate_id for c in candidates]
        assert len(ids) == len(set(ids))

    def test_no_contradictory_fvg_edge_cells(self) -> None:
        candidates = generate_candidates()
        assert not any(c.entry_point == EntryPoint.FVG_EDGE and not c.require_fvg for c in candidates)

    def test_fvg_edge_candidates_all_require_fvg(self) -> None:
        candidates = generate_candidates()
        fvg_edge_candidates = [c for c in candidates if c.entry_point == EntryPoint.FVG_EDGE]
        assert len(fvg_edge_candidates) > 0
        assert all(c.require_fvg for c in fvg_edge_candidates)


class TestStandardNormalCDF:
    def test_cdf_at_zero_is_half(self) -> None:
        assert _standard_normal_cdf(0.0) == pytest.approx(0.5)

    def test_cdf_matches_known_z_table_values(self) -> None:
        # Standard normal table constants.
        assert _standard_normal_cdf(1.0) == pytest.approx(0.8413447, abs=1e-6)
        assert _standard_normal_cdf(1.96) == pytest.approx(0.9750021, abs=1e-6)
        assert _standard_normal_cdf(3.0) == pytest.approx(0.9986501, abs=1e-6)

    def test_cdf_symmetry(self) -> None:
        for x in (0.5, 1.0, 2.5):
            assert _standard_normal_cdf(-x) == pytest.approx(1.0 - _standard_normal_cdf(x), abs=1e-9)


class TestOneSampleZTest:
    def test_fewer_than_two_values_returns_nan_and_p_one(self) -> None:
        z, p = one_sample_z_test([1.0])
        assert math.isnan(z)
        assert p == 1.0
        z, p = one_sample_z_test([])
        assert math.isnan(z)
        assert p == 1.0

    def test_zero_variance_returns_nan_and_p_one(self) -> None:
        z, p = one_sample_z_test([1.0, 1.0, 1.0, 1.0])
        assert math.isnan(z)
        assert p == 1.0

    def test_symmetric_zero_mean_sample(self) -> None:
        values = [1.0, -1.0] * 15  # n=30, mean exactly 0.0
        z, p = one_sample_z_test(values)
        assert z == pytest.approx(0.0, abs=1e-9)
        assert p == pytest.approx(1.0, abs=1e-9)

    def test_hand_computed_example(self) -> None:
        # mean=3.0, variance=((2-3)^2+(4-3)^2)/(2-1)=2.0, std_err=sqrt(2.0/2)=1.0, z=3.0
        z, p = one_sample_z_test([2.0, 4.0])
        assert z == pytest.approx(3.0, abs=1e-9)
        # Phi(3.0) = 0.99865 (standard normal table) -> p = 2*(1-0.99865)
        assert p == pytest.approx(2 * (1 - 0.9986501), abs=1e-6)


class TestBenjaminiHochberg:
    def test_all_significant(self) -> None:
        p_values = [0.001, 0.002, 0.003, 0.004, 0.005]
        assert benjamini_hochberg(p_values, q=0.05) == [True, True, True, True, True]

    def test_only_smallest_significant(self) -> None:
        p_values = [0.001, 0.2, 0.3, 0.4, 0.5]
        assert benjamini_hochberg(p_values, q=0.05) == [True, False, False, False, False]

    def test_none_significant(self) -> None:
        p_values = [0.1, 0.2, 0.3, 0.4, 0.5]
        assert benjamini_hochberg(p_values, q=0.05) == [False, False, False, False, False]

    def test_empty_input(self) -> None:
        assert benjamini_hochberg([], q=0.05) == []

    def test_unordered_input_matches_positional_order(self) -> None:
        # Same values as test_only_smallest_significant, shuffled -- result
        # must follow input order, not sorted order.
        p_values = [0.5, 0.001, 0.4, 0.2, 0.3]
        assert benjamini_hochberg(p_values, q=0.05) == [False, True, False, False, False]


class TestFDRPhaseGuards:
    def test_below_min_sample_size_excluded_even_with_tiny_p_value(self) -> None:
        records = [
            ScreeningRecord(
                candidate_id="too_few_trades",
                config=_dummy_config(candidate_id="too_few_trades"),
                total_trades=MIN_SAMPLE_SIZE - 1,
                p_value=0.0001,
                mean_r_multiple=0.5,
            ),
            ScreeningRecord(
                candidate_id="enough_trades",
                config=_dummy_config(candidate_id="enough_trades"),
                total_trades=MIN_SAMPLE_SIZE,
                p_value=0.0001,
                mean_r_multiple=0.5,
            ),
        ]
        apply_fdr_phase(records, q=0.05)
        assert records[0].fdr_discovery is False
        assert records[1].fdr_discovery is True

    def test_negative_mean_r_excluded_even_with_tiny_p_value(self) -> None:
        """Regression test: a confidently LOSING candidate (two-tailed z-test
        gives a tiny p-value for a consistently negative mean too) must never
        become an FDR 'discovery' -- Guarded Pattern Discovery only looks for
        profitable patterns, not statistically-confident bad ones."""
        records = [
            ScreeningRecord(
                candidate_id="confident_loser",
                config=_dummy_config(candidate_id="confident_loser"),
                total_trades=50,
                p_value=0.0001,
                mean_r_multiple=-0.5,
            )
        ]
        apply_fdr_phase(records, q=0.05)
        assert records[0].fdr_discovery is False

    def test_exactly_min_sample_size_is_eligible(self) -> None:
        records = [
            ScreeningRecord(
                candidate_id="exact",
                config=_dummy_config(candidate_id="exact"),
                total_trades=MIN_SAMPLE_SIZE,
                p_value=0.001,
                mean_r_multiple=0.3,
            )
        ]
        apply_fdr_phase(records, q=0.05)
        assert records[0].fdr_discovery is True


class TestScreenCandidatesConfigGuards:
    def test_rejects_leverage_set(self) -> None:
        config = BacktestConfig(
            initial_balance=10_000.0, risk_per_trade=0.01, spread=0.0001, commission=0.0, slippage=0.0,
            leverage=100.0,
        )
        with pytest.raises(ValueError):
            screen_candidates([], [_dummy_config()], "EURUSD", Timeframe.M15, config)

    def test_rejects_max_daily_loss_pct_set(self) -> None:
        config = BacktestConfig(
            initial_balance=10_000.0, risk_per_trade=0.01, spread=0.0001, commission=0.0, slippage=0.0,
            max_daily_loss_pct=0.05,
        )
        with pytest.raises(ValueError):
            screen_candidates([], [_dummy_config()], "EURUSD", Timeframe.M15, config)

    def test_rejects_max_equity_drawdown_pct_set(self) -> None:
        config = BacktestConfig(
            initial_balance=10_000.0, risk_per_trade=0.01, spread=0.0001, commission=0.0, slippage=0.0,
            max_equity_drawdown_pct=0.2,
        )
        with pytest.raises(ValueError):
            screen_candidates([], [_dummy_config()], "EURUSD", Timeframe.M15, config)


class TestSharedPipelineDifferentialParity:
    """Proves screen_candidates() (shared-pipeline runner) produces IDENTICAL
    results to BacktestEngine.run() for a single candidate -- the whole
    point of the shared-pipeline optimization is that it must not change
    trading behavior, only how fast it computes.
    """

    def test_matches_backtest_engine_exactly_on_real_data(self) -> None:
        provider = CSVDataProvider(filepath=FIXTURES_DIR / "eurusd_m15_continuation_sample.csv")
        bars = provider.load()
        provider.validate(bars)

        candidate = _dummy_config(
            candidate_id="bullish_fvg0_sweep0_ob_edge_tp2.0_none",
            ob_direction=OBDirection.BULLISH,
            require_fvg=False,
            require_liquidity_sweep=False,
            entry_point=EntryPoint.OB_EDGE,
            take_profit_r=2.0,
            trend_filter=TrendFilterMode.NONE,
        )
        backtest_config = BacktestConfig(
            initial_balance=10_000.0, risk_per_trade=0.01, spread=0.00005, commission=0.0, slippage=0.0,
        )

        # Baseline: BacktestEngine.run() wrapping one ParametrizedSMCStrategy.
        strategy = ParametrizedSMCStrategy(candidate)
        state_builder = MarketStateBuilder(symbol="EURUSD", timeframe=Timeframe.M15)
        strategy_engine = StrategyEngine()
        strategy_engine.register_strategy(strategy)
        engine = BacktestEngine(config=backtest_config)
        baseline = engine.run(bars, strategy_engine, state_builder)

        # Candidate: shared-pipeline runner with the same single candidate.
        shared_results = screen_candidates(bars, [candidate], "EURUSD", Timeframe.M15, backtest_config)
        shared = shared_results[candidate.candidate_id]

        assert len(baseline.trades) > 0, "Fixture must produce real trades for this to be a meaningful test"
        assert len(shared.trades) == len(baseline.trades)
        assert shared.win_rate == pytest.approx(baseline.win_rate)
        assert shared.profit_factor == pytest.approx(baseline.profit_factor)
        assert shared.total_profit == pytest.approx(baseline.total_profit)
        assert shared.max_drawdown == pytest.approx(baseline.max_drawdown)
        assert shared.final_balance == pytest.approx(baseline.final_balance)

        for expected, actual in zip(baseline.trades, shared.trades, strict=True):
            assert actual.entry_time == expected.entry_time
            assert actual.exit_time == expected.exit_time
            assert actual.direction == expected.direction
            assert actual.entry_price == pytest.approx(expected.entry_price)
            assert actual.exit_price == pytest.approx(expected.exit_price)
            assert actual.result == expected.result
            assert actual.pnl == pytest.approx(expected.pnl)
            assert actual.r_multiple == pytest.approx(expected.r_multiple)
