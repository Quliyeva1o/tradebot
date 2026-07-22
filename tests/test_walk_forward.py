"""Sprint 6a (Bug #48 fix) tests for research/walk_forward.py.

WalkForwardRunner previously imported and hardcoded BullishContinuationStrategy/
BearishContinuationStrategy directly, which blocked running walk-forward
analysis on any other strategy (e.g. NasdaqMidlineSweepStrategy, the one
proven strategy -- Sprint 6b). WalkForwardRunner.run() now takes a
strategy_factory: Callable[[], list[TradeSetupStrategy]] instead, matching
StrategyEngine's own DI convention (it only ever depends on the
TradeSetupStrategy interface, never a concrete strategy class).

Two things are verified here:
1. Regression: the factory-based path reproduces byte-identical fold output
   for the continuation strategies vs. the captured pre-fix baseline (this
   sprint changes *how* strategies are supplied, not the walk-forward
   algorithm or the strategies' own behavior).
2. Smoke test: NasdaqMidlineSweepStrategy can now be run through
   WalkForwardRunner via a factory, unblocking Sprint 6b. This is
   deliberately NOT the actual Midline Sweep validation study (that is
   Sprint 6b's separate deliverable) -- it only proves the tooling runs.
"""

from pathlib import Path

import pytest

from backtest.models import BacktestConfig
from core.models import Bar, Timeframe
from data.csv_provider import CSVDataProvider
from research.walk_forward import WalkForwardRunner
from strategy.continuation import BearishContinuationStrategy, BullishContinuationStrategy
from strategy.interfaces import TradeSetupStrategy
from strategy.nasdaq_midline_sweep import NasdaqMidlineSweepStrategy

FIXTURES_DIR = Path(__file__).parent / "fixtures"


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

    Baseline captured by running the exact pre-fix code (runner.run(StrategyConfig(),
    backtest_config), which internally hardcoded Bullish/BearishContinuationStrategy)
    against tests/fixtures/eurusd_m15_continuation_sample.csv, train_size_pct=0.5,
    val_size_pct=0.3, step_size_pct=0.3, before any Sprint 6a code changes were made.
    """

    def test_fold_output_matches_pre_fix_baseline(
        self, realistic_candles: list[Bar], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRADEBOT_ARTIFACTS_DIR", str(tmp_path))
        runner = WalkForwardRunner(
            realistic_candles, "EURUSD", Timeframe.M15,
            train_size_pct=0.5, val_size_pct=0.3, step_size_pct=0.3,
        )
        backtest_config = BacktestConfig(
            initial_balance=10000.0, risk_per_trade=0.01, spread=0.0001, commission=5.0, slippage=0.0,
        )

        folds = runner.run(_continuation_factory, backtest_config)

        assert len(folds) == 1
        fold = folds[0]
        assert fold["fold"] == 1
        assert fold["train_start_date"] == "2022-06-29 22:15"
        assert fold["train_end_date"] == "2022-08-26 14:00"
        assert fold["val_start_date"] == "2022-08-26 14:15"
        assert fold["val_end_date"] == "2022-09-30 14:00"
        assert fold["train_trades"] == 2
        assert fold["train_net_profit"] == pytest.approx(-216.02745849298975)
        assert fold["train_win_rate"] == pytest.approx(0.0)
        assert fold["val_trades"] == 0
        assert fold["val_net_profit"] == 0
        assert fold["val_win_rate"] == pytest.approx(0.0)

        # Diagnostics: exact rejection-reason counts, unchanged from the captured baseline.
        train_diag = fold["train_diagnostics"]
        assert train_diag["0_BullishContinuationStrategy"]["evaluations"] == 3978
        assert train_diag["0_BullishContinuationStrategy"]["setups_generated"] == 2
        assert train_diag["0_BullishContinuationStrategy"]["rejections"]["no_trend"] == 3039
        assert train_diag["1_BearishContinuationStrategy"]["evaluations"] == 3978
        assert train_diag["1_BearishContinuationStrategy"]["setups_generated"] == 0
        assert train_diag["1_BearishContinuationStrategy"]["rejections"]["no_trend"] == 2757
        val_diag = fold["val_diagnostics"]
        assert val_diag["0_BullishContinuationStrategy"]["evaluations"] == 2400
        assert val_diag["1_BearishContinuationStrategy"]["evaluations"] == 2400


class TestMidlineSweepSmokeTest:
    """Proves NasdaqMidlineSweepStrategy can now run through WalkForwardRunner via a factory.

    Unblocks Sprint 6b. NOT the Sprint 6b validation study itself.
    """

    def test_runs_without_error_and_produces_fold_shaped_output(
        self, realistic_candles: list[Bar], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRADEBOT_ARTIFACTS_DIR", str(tmp_path))
        runner = WalkForwardRunner(
            realistic_candles, "EURUSD", Timeframe.M15,
            train_size_pct=0.5, val_size_pct=0.3, step_size_pct=0.3,
        )
        backtest_config = BacktestConfig(
            initial_balance=10000.0, risk_per_trade=0.01, spread=0.0001, commission=5.0, slippage=0.0,
        )

        folds = runner.run(lambda: [NasdaqMidlineSweepStrategy()], backtest_config)

        assert len(folds) >= 1
        for fold in folds:
            assert "train_trades" in fold
            assert "val_trades" in fold
            assert "train_net_profit" in fold
            assert "val_net_profit" in fold
            assert "train_diagnostics" in fold
            assert "val_diagnostics" in fold
            assert isinstance(fold["train_trades"], int)
            assert isinstance(fold["val_trades"], int)
