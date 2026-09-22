"""The weekly live-vs-backtest report must follow the version-controlled Demo roster.

It used to decide which bots to report from Task Scheduler on the machine it
runs on. Since 2026-09-10 the bots run on the VPS, and every bot task on the
workstation -- where the report has to run, because it needs the history CSVs --
is deliberately Disabled. The 2026-09-14 report therefore skipped all nine bots
and printed "0 trades" while the account had closed five that week.
deploy/demo_roster.txt is the file install_tasks.ps1 already treats as the truth.
"""

from pathlib import Path

import scripts.live_vs_backtest_report as report

REPO = Path(__file__).parent.parent


def test_roster_reads_task_names_and_ignores_comments_and_blanks(tmp_path):
    """Same format install_tasks.ps1 reads: first token per line, # starts a comment."""
    roster = tmp_path / "demo_roster.txt"
    roster.write_text(
        "# Which *_Demo tasks may place real orders, and on whose account.\n"
        "\n"
        "OrbBreakout_XAUUSD_Demo   cfi          # 60m OR / M1 / 3R\n"
        "OrbSweep_GER40_Demo       fundingpips\n",
        encoding="utf-8",
    )

    assert report._roster(roster) == {"OrbBreakout_XAUUSD_Demo", "OrbSweep_GER40_Demo"}


def test_the_roster_is_read_per_account(tmp_path):
    """Real-order permission belongs to one account: the CFI bot's sizing, its 0.01-lot minimum
    and its stop-rule envelope were all measured on CFI, so it is not live on FundingPips."""
    roster = tmp_path / "demo_roster.txt"
    roster.write_text("OrbBreakout_XAUUSD_Demo   cfi\n"
                      "OrbSweep_GER40_Demo       fundingpips\n", encoding="utf-8")

    assert report._roster(roster, broker="cfi") == {"OrbBreakout_XAUUSD_Demo"}
    assert report._roster(roster, broker="fundingpips") == {"OrbSweep_GER40_Demo"}


def test_every_bot_in_the_real_roster_has_a_deployed_bat():
    """A roster name the report cannot map to a .bat would silently drop out of the report."""
    names = report._roster(REPO / "deploy" / "demo_roster.txt")
    bats = {report._task_name(b.name) for b in [*REPO.glob("run_live_orb_*_demo.bat"),
                                                *REPO.glob("run_live_fvg_*_demo.bat")]}

    assert names, "the real roster should not be empty"
    assert names <= bats


def test_task_names_follow_install_tasks_for_both_launcher_families():
    assert report._task_name("run_live_orb_breakoutwf_xauusd_demo.bat") == "OrbBreakoutwf_XAUUSD_Demo"
    assert report._task_name("run_live_fvg_window_ndx100_demo.bat") == "FvgWindow_NDX100_Demo"


def test_report_no_longer_consults_the_local_task_scheduler():
    """Local task state is exactly what went wrong once the bots moved to the VPS."""
    assert not hasattr(report, "_task_states")


def test_reverse_trades_are_labelled_apart_from_their_bots_own():
    """A 0.5R reverse trade mixed into the Breakout PF would hide what the strategy itself is doing."""
    assert report._strategy_label("setup_nasdaq_orb_m1__9f089d26") == "Breakout"
    assert report._strategy_label("setup_xauusd_orb_rev_d929727e") == "Sweep"
    assert report._strategy_label("setup_nasdaq_orb_m1_sar884987") == "Breakout reversal"
    assert report._strategy_label("setup_xauusd_orb_sar12884987") == "Sweep reversal"
    assert report._strategy_label("setup_fvg_window_US1_1a2b3c4d") == "FvgWindow"
    assert report._strategy_label("manual") is None


def test_the_report_sees_every_bot_the_roster_deploys():
    """It globbed run_live_orb_breakout_*_demo.bat, so the one bot left on 2026-09-20 --
    run_live_orb_breakoutwf_xauusd_demo.bat -- was invisible to the only check on it. The First FVG
    bot is not an ORB config at all, so it is seen through its own launchers."""
    roster = report._roster(REPO / "deploy" / "demo_roster.txt")
    seen = ({c.task for c in report.deployed_configs()}
            | {report._task_name(b.name) for b in report.deployed_fvg_launchers()})

    assert roster <= seen


def test_the_fvg_launcher_is_read_with_the_bots_own_parser():
    """The report and the stop-rule envelope must describe the flags the Demo bot really runs."""
    import run_live_first_fvg_window as runner

    [bat] = report.deployed_fvg_launchers()
    args = runner.launcher_args(bat)

    assert (args.symbol, args.tp_r, args.session_start, args.risk_per_trade_pct, args.paper) == (
        "NDX100", 3.0, "10:00", 0.0025, False)


def test_the_report_matches_live_deals_by_the_brokers_own_ticker():
    """A deal on the CFI account carries XAUUSD_, one on FundingPips carries XAUUSD. The
    launcher names neither -- the machine's own profile turns this repo's XAUUSD into both."""
    import config.brokers as machine

    wf = next(c for c in report.deployed_configs() if c.task == "OrbBreakoutwf_XAUUSD_Demo")
    assert (wf.symbol, wf.weekend_flat) == ("XAUUSD", True)

    assert machine.profiles()["cfi"].ticker(wf.symbol) == "XAUUSD_"
    assert machine.profiles()["fundingpips"].ticker(wf.symbol) == "XAUUSD"


def test_the_baseline_is_replayed_on_the_account_this_machine_trades():
    """The report counts the connected account's deals, so its baseline has to come from that
    same account's prices -- not from whichever broker a launcher used to name."""
    import config.brokers as machine
    from backtest.live_replay.brokers import deployed_broker

    for name in ("cfi", "fundingpips"):
        profile = machine.profiles()[name]
        assert deployed_broker(profile.name).name == name
