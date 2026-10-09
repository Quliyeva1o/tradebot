"""scripts/amd_backtest.py: the bootstrap that fixes the gold AMD stop rule, and the launcher it reads."""

from pathlib import Path

import numpy as np

import run_live_amd
from scripts import amd_backtest

REPO = Path(__file__).parent.parent


def test_the_backtest_runs_the_launchers_own_flags():
    args = run_live_amd.launcher_args(REPO / "run_live_amd_gold_xauusd_demo.bat")
    cfg = amd_backtest.LAUNCHER_CONFIG

    assert (args.symbol, args.paper, args.risk_per_trade_pct) == ("XAUUSD", False, 0.0025)
    assert (cfg.tp_r, cfg.min_fvg, cfg.spread_floor) == (args.tp_r, args.min_fvg, args.spread_floor)


def test_the_paper_twin_runs_the_same_strategy_flags():
    demo = run_live_amd.launcher_args(REPO / "run_live_amd_gold_xauusd_demo.bat")
    paper = run_live_amd.launcher_args(REPO / "run_live_amd_gold_xauusd_paper.bat")

    assert (demo.tp_r, demo.min_fvg, demo.spread_floor) == (paper.tp_r, paper.min_fvg, paper.spread_floor)
    assert paper.paper is True


def test_the_envelope_is_reproducible_and_widens_with_the_window():
    rs = np.array([2.0] * 6 + [-1.0] * 4)
    stops_a, floor_a = amd_backtest.envelope(rs, draws=5000)
    stops_b, floor_b = amd_backtest.envelope(rs, draws=5000)

    assert (stops_a, floor_a) == (stops_b, floor_b)
    assert stops_a[40] >= stops_a[20] > 0


def test_a_strategy_that_only_wins_has_no_drawdown_and_a_positive_checkpoint():
    stops, floor = amd_backtest.envelope(np.array([2.0, 2.0, 2.0]), draws=1000)

    assert stops == {20: 0.0, 40: 0.0}
    assert floor == 40.0
