"""The replay reads its configurations from the launchers both machines actually run."""

from pathlib import Path

import pytest

from backtest.live_replay.configs import (
    REPO,
    BotConfig,
    launcher_symbol,
    load_roster,
    parse_bat,
    scope,
    task_name,
)


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


SHARED_DEMO_BOTS = {"OrbBreakoutwf_XAUUSD_Demo", "FvgWindow_NDX100_Demo", "OrbBreakoutinv_DJI30_Demo",
                    "OrbBreakoutinv_SPX500_Demo", "OrbSweep_JP225_Demo"}
DEMO_BOTS_BY_BROKER = {"cfi": SHARED_DEMO_BOTS | {"OrbBreakout_GER40_Demo"},
                       "fundingpips": SHARED_DEMO_BOTS | {"OrbSweep_GER40_Demo"}}
DEMO_BOTS = DEMO_BOTS_BY_BROKER["cfi"] | DEMO_BOTS_BY_BROKER["fundingpips"]


def test_the_roster_lists_the_demo_bots_that_may_trade() -> None:
    """2026-09-20: cut from six to one. Five symbols lost over both the last 12 months and the
    last 3, so only the weekend-flat XAUUSD bot still placed real orders. 2026-09-22: the NDX100
    First FVG bot joined it, and later that day, on the user's call, every free symbol got its
    best paper bot over the last year -- on CFI first, then on FundingPips, whose own last year
    picked the same six. 2026-09-23: on CFI only, GER40 moved from the sweep to the 30m breakout,
    on the longer history. The roster file's own comment blocks carry the numbers."""
    assert load_roster(REPO) == {
        **dict.fromkeys(SHARED_DEMO_BOTS, frozenset({"cfi", "fundingpips"})),
        "OrbBreakout_GER40_Demo": frozenset({"cfi"}),
        "OrbSweep_GER40_Demo": frozenset({"fundingpips"}),
    }


def test_a_task_may_be_rostered_on_both_accounts_each_on_its_own_line(tmp_path: Path) -> None:
    roster = tmp_path / "deploy" / "demo_roster.txt"
    roster.parent.mkdir()
    roster.write_text("OrbSweep_GER40_Demo cfi\nOrbSweep_GER40_Demo fundingpips\nOrbSweep_JP225_Demo cfi\n",
                      encoding="utf-8")
    assert load_roster(tmp_path) == {"OrbSweep_GER40_Demo": {"cfi", "fundingpips"},
                                     "OrbSweep_JP225_Demo": {"cfi"}}


@pytest.mark.parametrize("broker", ["cfi", "fundingpips"])
def test_one_demo_bot_per_symbol(broker: str) -> None:
    """Two bots on one symbol read each other's position as foreign and block each other.

    Per account: GER40 has a different Demo bot on each broker, never two on one."""
    bots = {task for task, brokers in load_roster(REPO).items() if broker in brokers}
    assert bots == DEMO_BOTS_BY_BROKER[broker]
    symbols = [launcher_symbol(bat) for bat in [*REPO.glob("run_live_orb_*_demo.bat"),
                                                *REPO.glob("run_live_fvg_*_demo.bat")]
               if task_name(bat.name) in bots]
    assert sorted(symbols) == sorted(set(symbols)) and len(symbols) == len(bots)


def test_scope_is_every_distinct_configuration_the_repo_deploys() -> None:
    # The deployed ORB Demo bots, plus every Paper configuration that is not one of their twins.
    # A twin -- same CFI ticker, same parameters, only the risk differs -- is covered by its Demo
    # entry, as a twin always has been.
    assert {c.task for c in scope(REPO)} == {
        "OrbBreakoutwf_XAUUSD_Demo", "OrbBreakoutinv_DJI30_Demo", "OrbBreakoutinv_SPX500_Demo",
        "OrbSweep_GER40_Demo", "OrbBreakout_GER40_Demo", "OrbSweep_JP225_Demo",
        "OrbBreakout_XAUUSD_Paper", "OrbSweep_XAUUSD_Paper",
        "OrbBreakout_NDX100_Paper", "OrbBreakout_SPX500_Paper", "OrbBreakout_DJI30_Paper",
        "OrbBreakout_JP225_Paper",
        "OrbBreakoutinv_XAUUSD_Paper", "OrbBreakoutinv_NDX100_Paper",
        "OrbBreakoutinv_JP225_Paper", "OrbSweepinv_GER40_Paper",
    }


def test_every_live_bot_risks_a_quarter_percent() -> None:
    """2026-09-21: the XAUUSD bot halved before its first live trade -- its edge was already
    decaying inside the year it was chosen on, and a drawdown stop catches collapse, not decay.
    Every Demo bot since has evidence no stronger, so none risks more -- see the roster."""
    live = [c for c in scope(REPO) if not c.paper]
    assert {(c.task, c.risk_pct) for c in live} == {
        (task, 0.0025) for task in DEMO_BOTS if task != "FvgWindow_NDX100_Demo"}


def test_no_bot_that_places_real_orders_reverses_on_a_stop() -> None:
    """--reverse-on-stop 0.5 risks 1R to make 0.5R: it needs 66.7% wins and gets 62.3%."""
    assert {c.reverse_on_stop_r for c in scope(REPO) if not c.paper} == {None}
    assert not any("--reverse-on-stop" in p.read_text(encoding="utf-8")
                   for p in REPO.glob("run_live_orb_*.bat"))


def test_the_reverse_and_inverse_flags_are_parsed_and_make_different_configurations(tmp_path: Path) -> None:
    args = "run_live_nasdaq_orb.py --symbol XAUUSD --tp-r 4.0 --or-minutes 15 --risk-per-trade-pct 0.005"
    plain = parse_bat(_bat(tmp_path, "run_live_orb_breakout_xauusd_paper.bat", args + " --paper"))
    reverse = parse_bat(_bat(tmp_path, "run_live_orb_breakout_xauusd_demo.bat", args + " --reverse-on-stop 0.5"))
    inverse = parse_bat(_bat(tmp_path, "run_live_orb_breakoutinv_xauusd_paper.bat",
                             args + " --paper --inverse --variant inverse"))
    assert (plain.reverse_on_stop_r, plain.inverse) == (None, False)
    assert (reverse.reverse_on_stop_r, reverse.inverse) == (0.5, False)
    assert (inverse.task, inverse.inverse, inverse.reverse_on_stop_r) == ("OrbBreakoutinv_XAUUSD_Paper", True, None)
    assert len({plain.key, reverse.key, inverse.key}) == 3


def test_a_launcher_names_this_repos_symbol_not_a_brokers_ticker(tmp_path: Path) -> None:
    """One launcher set runs on both accounts, so a .bat cannot name CFI's XAUUSD_ or
    FundingPips' XAUUSD: each machine resolves its own from .env (config/brokers.py)."""
    cfg = parse_bat(_bat(tmp_path, "run_live_orb_breakoutwf_xauusd_demo.bat",
                         "run_live_nasdaq_orb.py --symbol XAUUSD --tp-r 3.0 --or-minutes 60 "
                         "--risk-per-trade-pct 0.005 --weekend-flat --variant weekendflat"))
    assert (cfg.symbol, cfg.task) == ("XAUUSD", "OrbBreakoutwf_XAUUSD_Demo")


def test_no_launcher_still_names_a_brokers_own_ticker() -> None:
    """A leftover XAUUSD_ polls a symbol that does not exist on the FundingPips machine -- the
    exact failure deploy/preflight.py was written about, one broker change earlier."""
    tickers = {"XAUUSD_", "US100_Spot", "US500_SPOT", "US30_SPOT", "GER30_SPOT", "JPN225_SPOT"}
    for path in sorted(REPO.glob("run_live_*.bat")):
        named = tickers & set(path.read_text(encoding="utf-8").split())
        assert not named, f"{path.name} still names {', '.join(sorted(named))}"


def test_the_roster_must_say_which_account_a_demo_bot_may_trade_on(tmp_path: Path) -> None:
    """Real-order permission is per account: this bot's sizing and stop rule were measured on
    CFI's spread, swap and 0.01-lot minimum, and none of that transfers to FundingPips."""
    roster = tmp_path / "deploy" / "demo_roster.txt"
    roster.parent.mkdir()
    roster.write_text("OrbBreakoutwf_XAUUSD_Demo\n", encoding="utf-8")

    with pytest.raises(ValueError, match="names no broker"):
        load_roster(tmp_path)


def test_scope_narrows_the_demo_side_to_one_accounts_bots(tmp_path: Path) -> None:
    """On the FundingPips machine no Demo bot is deployed, so its Paper twin must not be
    dropped as that Demo bot's duplicate -- it is the only measurement of it there."""
    demo = _bat(tmp_path, "run_live_orb_breakoutwf_xauusd_demo.bat",
                "run_live_nasdaq_orb.py --symbol XAUUSD --tp-r 3.0 --or-minutes 60 "
                "--risk-per-trade-pct 0.0025 --weekend-flat --variant weekendflat")
    _bat(tmp_path, "run_live_orb_breakoutwf_xauusd_paper.bat",
         "run_live_nasdaq_orb.py --symbol XAUUSD --tp-r 3.0 --or-minutes 60 "
         "--risk-per-trade-pct 0.005 --paper --weekend-flat --variant weekendflat")
    roster = tmp_path / "deploy" / "demo_roster.txt"
    roster.parent.mkdir()
    roster.write_text("OrbBreakoutwf_XAUUSD_Demo    cfi\n", encoding="utf-8")
    assert demo.exists()

    assert [c.task for c in scope(tmp_path, broker="cfi")] == ["OrbBreakoutwf_XAUUSD_Demo"]
    assert [c.task for c in scope(tmp_path, broker="fundingpips")] == ["OrbBreakoutwf_XAUUSD_Paper"]
