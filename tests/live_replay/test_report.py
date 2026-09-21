"""The report has to show both columns, every filter, and its own limitations."""

from datetime import UTC, date, datetime

from backtest.live_replay.configs import BotConfig
from backtest.live_replay.engine import TradeRecord
from scripts.live_replay_backtest import ConfigResult, render_report, write_trades_csv

CONFIG = BotConfig(task="OrbBreakout_NDX100_Demo", family="breakout", symbol="NDX100", paper=False,
                   risk_pct=0.005, scan_minutes=5, or_minutes=30, tp_r=4.0, entry_window_end=None)


def _trade(day: int, r: float, reason: str = "TP") -> TradeRecord:
    moment = datetime(2026, 9, day, 14, 10, tzinfo=UTC)
    return TradeRecord(task=CONFIG.task, symbol="NDX100", setup_id=f"s{day}", direction="BUY",
                       signal_time=moment, entry_time=moment, exit_time=moment, entry=29000.0,
                       stop=28900.0, target=29400.0, exit=29400.0, exit_reason=reason, volume=0.06,
                       pnl_usd=r * 250.0, swap_usd=-1.0, commission_usd=0.0, risk_usd=250.0, r=r,
                       balance_after=50_000.0 + r * 250.0, closed_on_entry_bar=False,
                       both_levels_touched=False)


def _result() -> ConfigResult:
    return ConfigResult(config=CONFIG, trades=[_trade(10, 4.0), _trade(11, -1.0, "SL")],
                        old=[(date(2026, 9, 10), 4.0), (date(2026, 9, 11), -1.0)],
                        ablation={"swap": 3.1, "spread": 3.4}, spread_ratio=1.04,
                        recorded_pf=1.329, reproduced_pf=1.331)


def test_the_report_puts_the_old_and_the_twin_side_by_side() -> None:
    text = render_report([_result()], end=date(2026, 9, 15), generated=datetime(2026, 9, 15, tzinfo=UTC),
                         validation_note="G1-G3: 40 passed", broker="cfi")
    assert "OrbBreakout_NDX100_Demo" in text
    assert "köhnə" in text
    assert "1.329" in text and "G1-G3: 40 passed" in text
    assert "swap" in text and "spread" in text          # the decomposition
    assert "məhdudiyyət" in text.lower()


def test_the_report_says_the_old_column_does_not_know_the_weekend_rule() -> None:
    from dataclasses import replace

    result = _result()
    result.config = replace(CONFIG, task="OrbBreakoutwf_XAUUSD_Paper", weekend_flat=True)
    text = render_report([result], end=date(2026, 9, 15), generated=datetime(2026, 9, 15, tzinfo=UTC),
                         validation_note="", broker="cfi")
    assert "həftə sonu" in text


def _render(result) -> str:
    return render_report([result], end=date(2026, 9, 15), generated=datetime(2026, 9, 15, tzinfo=UTC),
                         validation_note="", broker="cfi")


def test_the_report_explains_that_the_truth_lies_between_the_twin_and_the_swap_off_column() -> None:
    assert "sıfır faiz sərhədi" in _render(_result())


def test_a_bar_spread_that_understates_the_ticks_gets_a_sensitivity_line() -> None:
    result = _result()
    result.spread_ratio, result.spread_sensitivity = 1.13, (1.087, 70.0)
    text = _render(result)
    assert "ilə yenidən hesablandı" in text
    assert "1.13x" in text and "1.087" in text


def test_no_sensitivity_line_when_bar_and_tick_spreads_agree() -> None:
    assert "ilə yenidən hesablandı" not in _render(_result())  # ratio 1.04


def test_every_trade_is_written_to_the_csv(tmp_path) -> None:
    path = tmp_path / "trades.csv"
    write_trades_csv(path, [_trade(10, 4.0), _trade(11, -1.0, "SL")])
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert "setup_id" in lines[0] and "s10" in lines[1]
