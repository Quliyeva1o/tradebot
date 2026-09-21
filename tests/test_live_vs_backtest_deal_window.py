"""The weekly report's deal query must not end in the past on the broker's clock.

MT5 reads history_deals_get's bounds on the broker's wall clock, and on CFI that runs +3h ahead of
real UTC (measured 2026-09-21: tick epoch - time.time() = +10800 s). The report used to pass real
"now" as the upper bound, which ended the window three hours ago and silently dropped the newest
closed trades from every run.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import scripts.live_vs_backtest_report as report


def test_the_deal_window_reaches_past_the_brokers_clock_offset(monkeypatch) -> None:
    seen = {}

    def history_deals_get(frm, to):
        seen["frm"], seen["to"] = frm, to
        return []

    monkeypatch.setattr(report, "mt5", SimpleNamespace(
        initialize=lambda: True, shutdown=lambda: None, last_error=lambda: None,
        history_deals_get=history_deals_get))

    report.closed_live_trades(30)

    assert seen["to"] >= datetime.now(UTC) + timedelta(hours=3)
    assert seen["frm"] <= datetime.now(UTC) - timedelta(days=30)
