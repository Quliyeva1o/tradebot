"""Sprint 6a (Bug #48 fix) tests for research/robustness.py.

RobustnessTester previously imported and hardcoded BullishContinuationStrategy/
BearishContinuationStrategy directly, which blocked running robustness
stress-testing on any other strategy. RobustnessTester.run() now takes a
strategy_factory: Callable[[], list[TradeSetupStrategy]] (matching
StrategyEngine's own DI convention) plus an explicit pip_size parameter (the
slippage-stress scenario's pip_size was previously read off
strat_config.pip_size -- orthogonal to which strategy is under test, so it
is now passed directly instead of disappearing along with strat_config).

Two things are verified here:
1. Regression: the factory-based path reproduces byte-identical metrics for
   the continuation strategies vs. the captured pre-fix baseline (random's
   global seed is set explicitly, since the skipped-trades stress scenarios
   use unseeded random.random() -- without pinning the seed, even the
   pre-fix code would not reproduce those two fields run-to-run).
2. Smoke test: NasdaqMidlineSweepStrategy can now be run through
   RobustnessTester via a factory, unblocking Sprint 6b. This is
   deliberately NOT the actual Midline Sweep validation study (Sprint 6b's
   separate deliverable) -- it only proves the tooling runs.
"""

import random
from pathlib import Path

import pytest

from backtest.models import BacktestConfig
from core.models import Bar, Timeframe
from data.csv_provider import CSVDataProvider
from research.robustness import RobustnessTester
from strategy.continuation import BearishContinuationStrategy, BullishContinuationStrategy
from strategy.interfaces import TradeSetupStrategy
from strategy.nasdaq_midline_sweep import NasdaqMidlineSweepStrategy

FIXTURES_DIR = Path(__file__).parent / "fixtures"
DEFAULT_PIP_SIZE = 0.0001  # strategy.continuation.StrategyConfig.pip_size's default


@pytest.fixture(scope="module")
def realistic_candles() -> list[Bar]:
    """Same committed, deterministic 8000-bar EURUSD M15 slice used by tests/test_research.py."""
    provider = CSVDataProvider(filepath=FIXTURES_DIR / "eurusd_m15_continuation_sample.csv")
    bars = provider.load()
    provider.validate(bars)
    return bars


def _continuation_factory() -> list[TradeSetupStrategy]:
    return [BullishContinuationStrategy(), BearishContinuationStrategy()]


class TestContinuationRegression:
    """Byte-identical regression against the captured pre-Bug-#48-fix baseline.

    Baseline captured by running the exact pre-fix code (tester.run(StrategyConfig(),
    backtest_config), which internally hardcoded Bullish/BearishContinuationStrategy
    and read pip_size off strat_config) against
    tests/fixtures/eurusd_m15_continuation_sample.csv, with random.seed(1234) set
    immediately beforehand (see module docstring), before any Sprint 6a code changes
    were made.
    """

    def test_metrics_match_pre_fix_baseline(
        self, realistic_candles: list[Bar], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRADEBOT_ARTIFACTS_DIR", str(tmp_path))
        tester = RobustnessTester(realistic_candles, "EURUSD", Timeframe.M15)
        backtest_config = BacktestConfig(
            initial_balance=10000.0, risk_per_trade=0.01, spread=0.0001, commission=5.0, slippage=0.0,
        )

        random.seed(1234)
        metrics = tester.run(_continuation_factory, backtest_config, DEFAULT_PIP_SIZE)

        assert metrics["baseline"]["total_trades"] == 3
        assert metrics["baseline"]["net_profit"] == pytest.approx(-320.98492688241186)
        assert metrics["baseline"]["max_drawdown"] == pytest.approx(0.032098492688241276)

        assert metrics["high_spread"]["net_profit"] == pytest.approx(-337.5512892979387)
        assert metrics["high_commission"]["net_profit"] == pytest.approx(-335.8315666793965)
        assert metrics["high_slippage"]["net_profit"] == pytest.approx(-337.5512892979387)

        # Skip-stress scenarios use unseeded random.random() internally --
        # only reproducible because random.seed(1234) was set immediately above.
        assert metrics["skipped_10pct"]["net_profit"] == pytest.approx(-288.81150948788564)
        assert metrics["skipped_25pct"]["net_profit"] == pytest.approx(-253.54655714452724)

        # Diagnostics: exact rejection-reason counts, unchanged from the captured baseline,
        # EXCEPT no_trend (5913 -> 5890): a later fix to SwingDetector.detect_incremental
        # (it used to silently drop a bar's swing LOW whenever that same bar was ALSO a
        # swing high -- a "spike" bar -- unlike detect_batch, which has always kept both)
        # means MarketStructureEngine now sees swing lows it previously never got, so 23
        # more evaluations across this fixture correctly resolve a trend instead of
        # falling through to "no_trend". trades/net_profit/max_drawdown and every other
        # field below are unaffected on this fixture -- the swing fix is invisible except
        # in this rejection-reason bucketing.
        baseline_diag = metrics["baseline"]["diagnostics"]
        assert baseline_diag["0_BullishContinuationStrategy"]["evaluations"] == 7977
        assert baseline_diag["0_BullishContinuationStrategy"]["setups_generated"] == 2
        assert baseline_diag["0_BullishContinuationStrategy"]["rejections"]["no_trend"] == 5890
        assert baseline_diag["1_BearishContinuationStrategy"]["setups_generated"] == 1


class TestMidlineSweepSmokeTest:
    """Proves NasdaqMidlineSweepStrategy can now run through RobustnessTester via a factory.

    Unblocks Sprint 6b. NOT the Sprint 6b validation study itself.
    """

    def test_runs_without_error_and_produces_metrics_shaped_output(
        self, realistic_candles: list[Bar], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRADEBOT_ARTIFACTS_DIR", str(tmp_path))
        tester = RobustnessTester(realistic_candles, "EURUSD", Timeframe.M15)
        backtest_config = BacktestConfig(
            initial_balance=10000.0, risk_per_trade=0.01, spread=0.0001, commission=5.0, slippage=0.0,
        )

        metrics = tester.run(lambda: [NasdaqMidlineSweepStrategy()], backtest_config, DEFAULT_PIP_SIZE)

        for key in ("baseline", "high_spread", "high_commission", "high_slippage"):
            assert "net_profit" in metrics[key]
            assert "max_drawdown" in metrics[key]
            assert "total_trades" in metrics[key]
        assert "net_profit" in metrics["skipped_10pct"]
        assert "net_profit" in metrics["skipped_25pct"]
