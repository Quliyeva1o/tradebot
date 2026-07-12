"""Unit tests for research/run_strategy_backtest.py.

Covers the chronological in-sample/out-of-sample split (no overlap, correct
ratio, strict time ordering) and the --strategy -> strategy class mapping.
"""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.models import Bar, Timeframe
from data.csv_provider import CSVDataProvider
from research.run_strategy_backtest import (
    STRATEGY_REGISTRY,
    build_strategy,
    resolve_timeframe,
    split_bars,
)
from strategy.accumulation_breakout import AccumulationBreakoutStrategy
from strategy.continuation import BearishContinuationStrategy, BullishContinuationStrategy
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
