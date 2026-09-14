"""A bot's live trades must be reported even where there is no backtest data.

The VPS deliberately has no data/history CSVs -- the bots fetch their bars from
MT5 -- so run there on 2026-09-14 the report printed "backtest datasi yoxdur"
for every bot, skipped each bot's live trades along with it, and totalled
"0 trades" while the account had closed seven. Missing history should cost the
comparison, never the live record.
"""

import pytest

import scripts.live_vs_backtest_report as report

CFG = {"risk_pct": 0.005}
ROWS = [
    {"day": "2026-09-11", "reason": "SL", "entry": 29466.30, "exit": 29030.33, "profit": -87.19},
    {"day": "2026-09-10", "reason": "SL", "entry": 29210.38, "exit": 29029.08, "profit": -36.26},
]
BASE = {"pf": 1.318, "pf_1y": 1.083, "wr": 25.2, "green": 55.0, "per_month": 6.1}


def test_live_trades_are_printed_and_counted_without_backtest_data(capsys):
    """The VPS case: no baseline, two closed trades."""
    n, pnl = report._report_bot("NDX100", "Breakout", "30m OR / M5 / 4R", CFG, ROWS, base={})
    out = capsys.readouterr().out

    assert "backtest datasi yoxdur" in out
    assert "29466.30" in out
    assert "29210.38" in out
    assert n == 2
    assert pnl == pytest.approx(-123.45)


def test_no_verdict_is_given_without_a_baseline_to_compare_against(capsys):
    """Six losses with nothing to compare them to is not a divergence."""
    report._report_bot("NDX100", "Breakout", "30m OR / M5 / 4R", CFG, [dict(ROWS[0]) for _ in range(6)], base={})

    assert "DIQQET" not in capsys.readouterr().out


def test_bot_with_a_baseline_and_no_trades_adds_nothing_to_the_total(capsys):
    """The workstation case for a quiet bot is unchanged."""
    n, pnl = report._report_bot("SPX500", "Breakout", "60m OR / M1 / 4R", CFG, [], base=BASE)

    assert (n, pnl) == (0, 0.0)
    assert "BACKTEST gozlentisi" in capsys.readouterr().out
