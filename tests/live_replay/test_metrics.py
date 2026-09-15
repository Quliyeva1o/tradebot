"""PF, R, drawdown, half-year blocks and the three deployment filters."""

from datetime import date

import pytest

from backtest.live_replay.metrics import equity_curve, half_year_blocks, stats, three_filters


def test_profit_factor_and_drawdown_of_a_small_series() -> None:
    result = stats([2.0, -1.0, -1.0, 3.0])
    assert result.n == 4
    assert result.win_pct == 50.0
    assert result.pf == pytest.approx(2.5)
    assert result.net_r == pytest.approx(3.0)
    assert result.max_dd_r == pytest.approx(2.0)
    assert result.worst_streak == 2


def test_a_series_without_a_loss_has_no_finite_profit_factor() -> None:
    assert stats([1.0, 2.0]).pf == float("inf")


def test_an_empty_series_is_all_zeroes() -> None:
    assert stats([]).n == 0 and stats([]).pf == 0.0


def test_trades_are_grouped_into_calendar_half_years() -> None:
    blocks = half_year_blocks([(date(2025, 3, 1), 1.0), (date(2025, 8, 1), -1.0),
                               (date(2026, 1, 5), 2.0)])
    assert list(blocks) == ["2025H1", "2025H2", "2026H1"]


def test_the_three_filters_read_each_rule_separately() -> None:
    dated = [(date(2024, 3, 1), 3.0), (date(2024, 9, 1), -1.0), (date(2025, 3, 1), 2.0),
             (date(2025, 9, 1), -1.0), (date(2026, 3, 1), 2.0), (date(2026, 9, 1), 1.0)]
    result = three_filters(dated, end=date(2026, 9, 15))
    assert result.full_ok is True
    assert result.last_year_ok is True
    assert result.blocks_green_pct == pytest.approx(100.0 * 4 / 6)
    assert result.blocks_ok is True
    assert result.passed is True
    assert result.short_history is False


def test_a_symbol_with_under_thirty_months_of_trades_is_flagged_short() -> None:
    dated = [(date(2025, 6, 1), 1.0), (date(2026, 6, 1), 1.0)]
    assert three_filters(dated, end=date(2026, 9, 15)).short_history is True


def test_the_dollar_curve_reports_the_worst_peak_to_trough() -> None:
    final, dd = equity_curve([1000.0, -2000.0, 500.0], 50_000.0)
    assert final == pytest.approx(49_500.0)
    assert dd == pytest.approx(100 * 2000 / 51_000, abs=0.01)
