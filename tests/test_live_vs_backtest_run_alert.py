"""The weekly report must say so, down the alert channel, when it could not check the stop rule.

A report that crashes is a stop rule nobody checked that week, and Task Scheduler records the
failure somewhere no one reads. run() sends it as an alert and re-raises, so the task's own result
still reads as a failure.
"""

import pytest

import scripts.live_vs_backtest_report as report


def _capture_alerts(monkeypatch, delivered: bool = True) -> list[str]:
    sent: list[str] = []

    def fake_send(text):
        if text:
            sent.append(text)
            return delivered
        return None

    monkeypatch.setattr(report.kill_rule, "send_alert", fake_send)
    return sent


def test_a_crashing_report_alerts_and_still_fails(monkeypatch, capsys) -> None:
    sent = _capture_alerts(monkeypatch)

    def crash() -> None:
        raise RuntimeError("MT5 initialize failed: secret detail")

    monkeypatch.setattr(report, "main", crash)

    with pytest.raises(RuntimeError):
        report.run()

    assert len(sent) == 1
    assert "ISLEMEDI" in sent[0] and "RuntimeError" in sent[0]
    assert "secret detail" not in sent[0]            # only the exception's type goes out
    assert "gonderildi" in capsys.readouterr().out


def test_a_clean_report_sends_nothing(monkeypatch) -> None:
    sent = _capture_alerts(monkeypatch)
    monkeypatch.setattr(report, "main", lambda: None)

    report.run()

    assert sent == []


def test_an_alert_that_did_not_go_out_is_printed_not_hidden(capsys) -> None:
    report._print_alert_outcome(False)

    assert "GONDERILMEDI" in capsys.readouterr().out


def test_nothing_to_send_prints_nothing(capsys) -> None:
    report._print_alert_outcome(None)

    assert capsys.readouterr().out == ""
