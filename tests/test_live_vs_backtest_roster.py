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
        "# Which *_Demo tasks may place real orders.\n"
        "\n"
        "OrbBreakout_XAUUSD_Demo      # 60m OR / M1 / 3R\n"
        "OrbSweep_GER40_Demo\n",
        encoding="utf-8",
    )

    assert report._roster(roster) == {"OrbBreakout_XAUUSD_Demo", "OrbSweep_GER40_Demo"}


def test_every_bot_in_the_real_roster_has_a_deployed_bat():
    """A roster name the report cannot map to a .bat would silently drop out of the report."""
    names = report._roster(REPO / "deploy" / "demo_roster.txt")
    bats = {report._task_name(b.name) for b in REPO.glob("run_live_orb_*_demo.bat")}

    assert names, "the real roster should not be empty"
    assert names <= bats


def test_report_no_longer_consults_the_local_task_scheduler():
    """Local task state is exactly what went wrong once the bots moved to the VPS."""
    assert not hasattr(report, "_task_states")
