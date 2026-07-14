"""Unit tests for research/run_strategy_backtest.py.

Covers the chronological in-sample/out-of-sample split (no overlap, correct
ratio, strict time ordering) and the --strategy -> strategy class mapping.
"""

from datetime import datetime, time, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.models import Bar, Timeframe
from data.csv_provider import CSVDataProvider
from research.run_strategy_backtest import (
    STRATEGY_REGISTRY,
    _coerce_time_params,
    build_strategy,
    measure_avg_bars_per_day,
    resolve_timeframe,
    run_backtest,
    split_bars,
)
from strategy.accumulation_breakout import AccumulationBreakoutStrategy
from strategy.continuation import BearishContinuationStrategy, BullishContinuationStrategy
from strategy.manipulation_reversal import ManipulationReversalStrategy
from strategy.nasdaq_midline_sweep import NasdaqMidlineSweepStrategy
from strategy.opening_range_breakout import OpeningRangeBreakoutStrategy
from strategy.order_block_retest import OrderBlockRetestStrategy


def _write_synthetic_csv(path: Path, n_bars: int = 100) -> Path:
    csv_file = path / "synthetic.csv"
    lines = ["time,open,high,low,close,volume"]
    t = datetime(2026, 1, 1, 0, 0)
    price = 1.1000
    for i in range(n_bars):
        o = price
        h = price + 0.0010
        low = price - 0.0010
        c = price + (0.0002 if i % 2 == 0 else -0.0002)
        lines.append(f"{t.isoformat(sep=' ')},{o:.5f},{h:.5f},{low:.5f},{c:.5f},100")
        price = c
        t += timedelta(minutes=15)
    csv_file.write_text("\n".join(lines) + "\n")
    return csv_file


@pytest.fixture
def synthetic_bars(tmp_path: Path) -> list[Bar]:
    csv_path = _write_synthetic_csv(tmp_path, n_bars=100)
    provider = CSVDataProvider(filepath=csv_path)
    return provider.load()


class TestSplitBars:
    def test_full_split_returns_all_bars(self, synthetic_bars: list[Bar]) -> None:
        result = split_bars(synthetic_bars, "full", 0.7)
        assert result == synthetic_bars

    def test_in_sample_out_of_sample_do_not_overlap(self, synthetic_bars: list[Bar]) -> None:
        in_sample = split_bars(synthetic_bars, "in_sample", 0.7)
        out_of_sample = split_bars(synthetic_bars, "out_of_sample", 0.7)

        in_sample_ts = {bar.timestamp for bar in in_sample}
        out_of_sample_ts = {bar.timestamp for bar in out_of_sample}

        assert in_sample_ts.isdisjoint(out_of_sample_ts)
        assert len(in_sample) + len(out_of_sample) == len(synthetic_bars)

    def test_split_ratio_is_respected(self, synthetic_bars: list[Bar]) -> None:
        split_ratio = 0.7
        in_sample = split_bars(synthetic_bars, "in_sample", split_ratio)
        out_of_sample = split_bars(synthetic_bars, "out_of_sample", split_ratio)

        expected_in_sample_len = int(len(synthetic_bars) * split_ratio)
        assert len(in_sample) == expected_in_sample_len
        assert len(out_of_sample) == len(synthetic_bars) - expected_in_sample_len

    @pytest.mark.parametrize("split_ratio", [0.1, 0.5, 0.9])
    def test_split_ratio_is_respected_for_various_ratios(
        self, synthetic_bars: list[Bar], split_ratio: float
    ) -> None:
        in_sample = split_bars(synthetic_bars, "in_sample", split_ratio)
        expected_len = int(len(synthetic_bars) * split_ratio)
        assert len(in_sample) == expected_len

    def test_chronological_order_is_preserved(self, synthetic_bars: list[Bar]) -> None:
        in_sample = split_bars(synthetic_bars, "in_sample", 0.7)
        out_of_sample = split_bars(synthetic_bars, "out_of_sample", 0.7)

        assert max(bar.timestamp for bar in in_sample) < min(bar.timestamp for bar in out_of_sample)

    def test_bars_within_each_split_remain_in_original_order(
        self, synthetic_bars: list[Bar]
    ) -> None:
        in_sample = split_bars(synthetic_bars, "in_sample", 0.7)
        out_of_sample = split_bars(synthetic_bars, "out_of_sample", 0.7)

        assert in_sample == synthetic_bars[: len(in_sample)]
        assert out_of_sample == synthetic_bars[len(in_sample) :]
        assert list(in_sample) == sorted(in_sample, key=lambda b: b.timestamp)
        assert list(out_of_sample) == sorted(out_of_sample, key=lambda b: b.timestamp)


class TestStrategySelection:
    @pytest.mark.parametrize(
        ("strategy_name", "expected_cls"),
        [
            ("accumulation_breakout", AccumulationBreakoutStrategy),
            ("midline_sweep", NasdaqMidlineSweepStrategy),
            ("opening_range_breakout", OpeningRangeBreakoutStrategy),
            ("order_block_retest", OrderBlockRetestStrategy),
            ("continuation_bullish", BullishContinuationStrategy),
            ("continuation_bearish", BearishContinuationStrategy),
            ("manipulation_reversal", ManipulationReversalStrategy),
        ],
    )
    def test_registry_maps_name_to_expected_class(
        self, strategy_name: str, expected_cls: type
    ) -> None:
        assert STRATEGY_REGISTRY[strategy_name] is expected_cls

    @pytest.mark.parametrize("strategy_name", list(STRATEGY_REGISTRY.keys()))
    def test_build_strategy_constructs_registered_class(self, strategy_name: str) -> None:
        strategy = build_strategy(strategy_name, {})
        assert isinstance(strategy, STRATEGY_REGISTRY[strategy_name])

    @pytest.mark.parametrize("strategy_name", list(STRATEGY_REGISTRY.keys()))
    def test_build_strategy_instantiates_the_patched_class_with_params(
        self, strategy_name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = MagicMock()
        monkeypatch.setitem(STRATEGY_REGISTRY, strategy_name, mock_cls)

        params = {"risk_reward": 3.0}
        result = build_strategy(strategy_name, params)

        mock_cls.assert_called_once_with(risk_reward=3.0)
        assert result is mock_cls.return_value


class TestResolveTimeframe:
    def test_valid_timeframe_resolves_to_enum_member(self) -> None:
        assert resolve_timeframe("M15") is Timeframe.M15

    def test_invalid_timeframe_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown timeframe"):
            resolve_timeframe("NOT_A_TIMEFRAME")


class TestCoerceTimeParams:
    def test_converts_known_time_field_strings(self) -> None:
        coerced = _coerce_time_params({"day_session_end": "16:00", "risk_reward": 3.0})
        assert coerced["day_session_end"] == time(16, 0)
        assert coerced["risk_reward"] == 3.0

    @pytest.mark.parametrize(
        "field_name",
        [
            "session_start",
            "session_end",
            "build_session_start",
            "build_session_end",
            "day_session_end",
            "reference_time",
        ],
    )
    def test_converts_every_known_time_field(self, field_name: str) -> None:
        coerced = _coerce_time_params({field_name: "09:30"})
        assert coerced[field_name] == time(9, 30)

    def test_leaves_non_time_params_untouched(self) -> None:
        params = {"risk_reward": 2.0, "limit_holding_to_session": True}
        assert _coerce_time_params(params) == params

    def test_leaves_already_time_typed_values_untouched(self) -> None:
        coerced = _coerce_time_params({"day_session_end": time(16, 0)})
        assert coerced["day_session_end"] == time(16, 0)

    def test_does_not_mutate_input_dict(self) -> None:
        params = {"day_session_end": "16:00"}
        _coerce_time_params(params)
        assert params["day_session_end"] == "16:00"


class TestMeasureAvgBarsPerDay:
    def test_returns_none_for_fewer_than_two_bars(self) -> None:
        assert measure_avg_bars_per_day([]) is None

    def test_computes_average_from_timestamp_span(self, synthetic_bars: list[Bar]) -> None:
        # 100 bars, 15 minutes apart -> span = 99*15 = 1485 minutes = 1.03125 days.
        result = measure_avg_bars_per_day(synthetic_bars)
        expected_span_days = (99 * 15) / (24 * 60)
        assert result == pytest.approx(100 / expected_span_days)

    def test_single_bar_returns_none(self) -> None:
        bar = Bar(
            timestamp=datetime(2026, 1, 1), open=1.0, high=1.0, low=1.0, close=1.0, volume=0.0
        )
        assert measure_avg_bars_per_day([bar]) is None


class TestMaxHoldingBarsResolution:
    """End-to-end (via run_backtest) verification that max_holding_bars
    resolution is regression-free: strategies default to None (unlimited)
    unless explicitly opted in, and an explicit --max-holding-bars always
    wins over the strategy's own recommendation.
    """

    def _run(self, tmp_path: Path, strategy_name: str, params: dict, max_holding_bars=None) -> dict:
        csv_path = _write_synthetic_csv(tmp_path, n_bars=50)
        return run_backtest(
            strategy_name=strategy_name,
            data_file=str(csv_path),
            timeframe=Timeframe.M15,
            params=params,
            split="full",
            split_ratio=0.7,
            initial_balance=10_000.0,
            spread=0.0002,
            commission=0.0,
            risk_per_trade=0.01,
            max_holding_bars=max_holding_bars,
        )

    def test_default_strategy_with_no_override_yields_none(self, tmp_path: Path) -> None:
        output = self._run(tmp_path, "order_block_retest", {})
        assert output["max_holding_bars"] is None

    def test_opt_in_strategy_with_no_cli_override_uses_recommendation(self, tmp_path: Path) -> None:
        output = self._run(tmp_path, "accumulation_breakout", {"limit_holding_to_session": True})
        # Default session 09:30-11:00 = 90 minutes; at M15 (this test's timeframe) = 6 bars.
        assert output["max_holding_bars"] == 6

    def test_explicit_cli_override_always_wins(self, tmp_path: Path) -> None:
        output = self._run(
            tmp_path,
            "accumulation_breakout",
            {"limit_holding_to_session": True},
            max_holding_bars=5,
        )
        assert output["max_holding_bars"] == 5

    def test_explicit_cli_override_wins_even_without_strategy_opt_in(self, tmp_path: Path) -> None:
        output = self._run(tmp_path, "order_block_retest", {}, max_holding_bars=10)
        assert output["max_holding_bars"] == 10

    def test_output_includes_measured_avg_bars_per_day(self, tmp_path: Path) -> None:
        output = self._run(tmp_path, "order_block_retest", {})
        assert output["measured_avg_bars_per_day"] is not None
        assert output["measured_avg_bars_per_day"] > 0
