"""The replay reads its configurations from the launchers the VPS actually runs."""

from pathlib import Path

from backtest.live_replay.configs import REPO, BotConfig, load_roster, parse_bat, scope


def _bat(tmp_path: Path, name: str, args: str) -> Path:
    path = tmp_path / name
    path.write_text(f'@echo off\ncd /d "%~dp0"\n".venv\\Scripts\\python.exe" {args}\n', encoding="utf-8")
    return path


def test_a_breakout_launcher_is_parsed(tmp_path: Path) -> None:
    cfg = parse_bat(_bat(tmp_path, "run_live_orb_breakout_ndx100_demo.bat",
                         "run_live_nasdaq_orb.py --symbol NDX100 --tp-r 4.0 --or-minutes 30 "
                         "--scan-timeframe M5 --risk-per-trade-pct 0.005"))
    assert cfg == BotConfig(task="OrbBreakout_NDX100_Demo", family="breakout", symbol="NDX100",
                            paper=False, risk_pct=0.005, scan_minutes=5, or_minutes=30, tp_r=4.0,
                            entry_window_end=None)


def test_breakout_defaults_match_the_runners_own(tmp_path: Path) -> None:
    cfg = parse_bat(_bat(tmp_path, "run_live_orb_breakout_jp225_paper.bat",
                         "run_live_nasdaq_orb.py --symbol JP225 --tp-r 4.0 --risk-per-trade-pct 0.005 --paper"))
    assert (cfg.scan_minutes, cfg.or_minutes, cfg.paper) == (1, 15, True)


def test_a_sweep_launcher_is_parsed(tmp_path: Path) -> None:
    cfg = parse_bat(_bat(tmp_path, "run_live_orb_sweep_ger40_demo.bat",
                         "run_live_xauusd_orb.py --symbol GER40 --timeframe M15 --risk-per-trade-pct 0.005"))
    assert cfg == BotConfig(task="OrbSweep_GER40_Demo", family="sweep", symbol="GER40", paper=False,
                            risk_pct=0.005, scan_minutes=15, or_minutes=None, tp_r=None,
                            entry_window_end=None)


def test_a_paper_twin_of_a_deployed_demo_bot_is_the_same_configuration(tmp_path: Path) -> None:
    demo = parse_bat(_bat(tmp_path, "run_live_orb_breakout_dji30_demo.bat",
                          "run_live_nasdaq_orb.py --symbol DJI30 --tp-r 4.0 --or-minutes 60 --risk-per-trade-pct 0.005"))
    paper = parse_bat(_bat(tmp_path, "run_live_orb_breakout_dji30_paper.bat",
                           "run_live_nasdaq_orb.py --symbol DJI30 --tp-r 4.0 --or-minutes 60 --risk-per-trade-pct 0.005 --paper"))
    assert demo.key == paper.key and demo.task != paper.task


def test_the_weekend_flat_launcher_is_a_breakout_with_the_rule_on(tmp_path: Path) -> None:
    cfg = parse_bat(_bat(tmp_path, "run_live_orb_breakoutwf_xauusd_paper.bat",
                         "run_live_nasdaq_orb.py --symbol XAUUSD --tp-r 3.0 --or-minutes 60 "
                         "--risk-per-trade-pct 0.005 --paper --weekend-flat --variant weekendflat"))
    assert (cfg.task, cfg.family, cfg.weekend_flat, cfg.or_minutes, cfg.tp_r) == (
        "OrbBreakoutwf_XAUUSD_Paper", "breakout", True, 60, 3.0)


def test_the_family_comes_from_the_runner_a_launcher_calls(tmp_path: Path) -> None:
    cfg = parse_bat(_bat(tmp_path, "run_live_orb_anyname_ger40_paper.bat",
                         "run_live_xauusd_orb.py --symbol GER40 --timeframe M15 --risk-per-trade-pct 0.005 --paper"))
    assert cfg.family == "sweep" and cfg.weekend_flat is False


def test_a_weekend_flat_twin_is_a_different_configuration(tmp_path: Path) -> None:
    plain = parse_bat(_bat(tmp_path, "run_live_orb_breakout_xauusd_paper.bat",
                           "run_live_nasdaq_orb.py --symbol XAUUSD --tp-r 3.0 --or-minutes 60 --risk-per-trade-pct 0.005 --paper"))
    flat = parse_bat(_bat(tmp_path, "run_live_orb_breakoutwf_xauusd_paper.bat",
                          "run_live_nasdaq_orb.py --symbol XAUUSD --tp-r 3.0 --or-minutes 60 --risk-per-trade-pct 0.005 --paper --weekend-flat"))
    assert plain.key != flat.key


def test_the_roster_lists_the_six_demo_bots_that_may_trade() -> None:
    assert load_roster(REPO) == {
        "OrbBreakout_XAUUSD_Demo", "OrbBreakout_NDX100_Demo", "OrbBreakout_SPX500_Demo",
        "OrbBreakout_DJI30_Demo", "OrbSweep_GER40_Demo", "OrbBreakout_JP225_Demo",
    }


def test_scope_is_every_distinct_configuration_the_repo_deploys() -> None:
    assert {c.task for c in scope(REPO)} == {
        "OrbBreakout_XAUUSD_Demo", "OrbBreakout_NDX100_Demo", "OrbBreakout_SPX500_Demo",
        "OrbBreakout_DJI30_Demo", "OrbBreakout_JP225_Demo", "OrbSweep_GER40_Demo",
        "OrbBreakout_XAUUSD_Paper", "OrbBreakout_GER40_Paper", "OrbSweep_XAUUSD_Paper",
        "OrbSweep_JP225_Paper", "OrbBreakoutwf_XAUUSD_Paper",
    }
