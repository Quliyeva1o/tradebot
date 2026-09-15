"""The replay must reproduce the trades the bots really took, 2026-09-10..14.

Fixtures: tests/fixtures/live_replay/live_deals_2026_09.csv (the dead account 40000281947's own
MT5 deal history) and paper_trades_2026_09.csv (the VPS paper state files). The 2026-09-07..09
deals ran other configurations, a hand-moved target and the pre-0825aff gold tick value, so
they are recorded but not validated.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backtest.live_replay.configs import BotConfig
from backtest.live_replay.engine import run
from backtest.live_replay.market import DEFAULT_DATA_DIR, load_fx, load_m1
from backtest.live_replay.specs import load_specs
from backtest.live_replay.ticks import TickCache

BROKER_TZ = ZoneInfo("Europe/Bucharest")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "live_replay"
REPLAY_FROM = datetime(2026, 8, 24, tzinfo=UTC)
REPLAY_TO = datetime(2026, 9, 16, tzinfo=UTC)
WINDOW = (datetime(2026, 9, 10, tzinfo=BROKER_TZ), datetime(2026, 9, 15, tzinfo=BROKER_TZ))
# Exit prices: one spread plus a little slack, in price units, from the 2026 spread column.
ENTRY_TOLERANCE = {"NDX100": 3.5, "SPX500": 1.8, "DJI30": 2.5, "GER40": 4.5, "JP225": 12.0,
                   "XAUUSD": 0.3}
# Entry prices cannot be judged by a fixed tolerance: the real orders filled 4-7 s into their
# minute, and JP225/NDX100 moved up to 15 points inside that window. The replay fills at one
# fixed latency, so its entry must lie within the quotes of that window, plus ~0.3 spread of
# slippage. A replay reading the wrong minute or a stale bar open would fall outside it.
LATENCY_SECONDS = range(4, 8)
SLIPPAGE = {"NDX100": 1.0, "SPX500": 0.4, "DJI30": 0.6, "GER40": 0.6, "JP225": 3.0, "XAUUSD": 0.05}
TICKS = TickCache(DEFAULT_DATA_DIR / "ticks")


def _quote_range(symbol: str, side: str, entry_time: datetime) -> tuple[float, float]:
    """Lowest and highest fill price the order could have got across :04-:07 of its minute."""
    minute = int(entry_time.timestamp()) // 60 * 60
    quotes = [q for q in (TICKS.entry_quote(symbol, minute, s) for s in LATENCY_SECONDS) if q]
    prices = [ask if side == "BUY" else bid for bid, ask in quotes]
    return min(prices), max(prices)
DATA_PRESENT = all((DEFAULT_DATA_DIR / f"{s}_M1.csv").exists()
                   for s in ENTRY_TOLERANCE) and (DEFAULT_DATA_DIR / "USDJPY_D1.csv").exists()


def _config(symbol: str, family: str, scan: int, or_minutes: int | None, tp_r: float | None) -> BotConfig:
    return BotConfig(task=f"{symbol}_{family}", family=family, symbol=symbol, paper=False,
                     risk_pct=0.005, scan_minutes=scan, or_minutes=or_minutes, tp_r=tp_r,
                     entry_window_end=None)


# What each Demo bot actually ran that week. XAUUSD swapped 60m/3R -> 15m/4R on 2026-09-14
# (commit 190b319), so both are replayed and their trades filtered by date.
DEMO_CONFIGS = [
    _config("JP225", "breakout", 1, 15, 4.0),
    _config("NDX100", "breakout", 5, 30, 4.0),
    _config("SPX500", "breakout", 1, 60, 4.0),
    _config("DJI30", "breakout", 1, 60, 4.0),
    _config("GER40", "sweep", 15, None, None),
    _config("XAUUSD", "breakout", 1, 60, 3.0),
    _config("XAUUSD", "breakout", 1, 15, 4.0),
]
PAPER_CONFIGS = [
    _config("NDX100", "breakout", 5, 30, 4.0),
    _config("XAUUSD", "breakout", 1, 15, 4.0),
    _config("XAUUSD", "breakout", 1, 60, 3.0),
]


def _rows(name: str) -> list[dict]:
    with (FIXTURES / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _server(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=BROKER_TZ)


def _in_window(moment: datetime) -> bool:
    return WINDOW[0] <= moment < WINDOW[1]


def _key(config: BotConfig) -> str:
    return f"{config.symbol}_{config.or_minutes}_{config.tp_r}_{config.family}"


@pytest.fixture(scope="module")
def replayed() -> dict[str, list]:
    specs = load_specs()
    ticks = TickCache(DEFAULT_DATA_DIR / "ticks")
    out: dict[str, list] = {}
    for config in DEMO_CONFIGS + PAPER_CONFIGS:
        if _key(config) in out:
            continue
        m1 = load_m1(config.symbol, DEFAULT_DATA_DIR, start=REPLAY_FROM, end=REPLAY_TO)
        spec = specs[config.symbol]
        out[_key(config)] = run(config, m1, spec, load_fx(spec.profit_currency), ticks=ticks)
    return out


def _replayed_for(replayed, config, *, days=None):
    trades = [t for t in replayed[_key(config)] if _in_window(t.entry_time.astimezone(BROKER_TZ))]
    if days is not None:
        trades = [t for t in trades if t.entry_time.astimezone(BROKER_TZ).day in days]
    return sorted(trades, key=lambda t: t.entry_time)


@pytest.mark.skipif(not DATA_PRESENT, reason="needs refreshed data/history/fundingpips")
@pytest.mark.parametrize("symbol", ["JP225", "NDX100", "SPX500", "DJI30", "GER40", "XAUUSD"])
def test_the_replay_takes_the_same_demo_trades_that_week(replayed, symbol) -> None:
    real = [r for r in _rows("live_deals_2026_09.csv")
            if r["symbol"] == symbol and r["use_in_validation"] in ("yes", "entry_only")
            and _in_window(_server(r["open_time_srv"]))]
    if symbol == "XAUUSD":
        mine = (_replayed_for(replayed, _config("XAUUSD", "breakout", 1, 60, 3.0), days={10, 11, 12, 13})
                + _replayed_for(replayed, _config("XAUUSD", "breakout", 1, 15, 4.0), days={14}))
    else:
        config = next(c for c in DEMO_CONFIGS if c.symbol == symbol)
        mine = _replayed_for(replayed, config)

    assert len(mine) == len(real), (
        f"{symbol}: replay took {[t.entry_time.astimezone(BROKER_TZ).strftime('%m-%d %H:%M') for t in mine]}, "
        f"live took {[r['open_time_srv'] for r in real]}")

    for trade, row in zip(mine, sorted(real, key=lambda r: r["open_time_srv"]), strict=True):
        where = f"{symbol} {row['open_time_srv']}"
        assert abs((trade.entry_time - _server(row["open_time_srv"])).total_seconds()) <= 120, where
        real_entry = float(row["open_price"])
        low, high = _quote_range(symbol, row["side"], trade.entry_time)
        assert abs(trade.entry - real_entry) <= (high - low) + SLIPPAGE[symbol], (
            f"{where}: replay {trade.entry} vs real {real_entry}; quotes :04-:07 spanned {low}..{high}")
        assert trade.stop == pytest.approx(float(row["sl"]), abs=0.011), where
        assert trade.target == pytest.approx(float(row["tp"]), abs=0.011), where
        assert trade.direction == row["side"], where

        if row["use_in_validation"] == "entry_only":
            # FundingPips liquidated these at 23:02:25; the replay keeps holding them.
            assert trade.exit_time >= _server(row["close_time_srv"]), where
            continue

        real_exit = float(row["close_price"])
        if row["close_reason"] == "SL_GAP":
            sign = 1.0 if row["side"] == "BUY" else -1.0
            # Price-only R on both sides: trade.r also carries swap (-0.13R on the NDX100 weekend).
            real_r = sign * (real_exit - float(row["open_price"])) / abs(
                float(row["open_price"]) - float(row["sl"]))
            replay_r = sign * (trade.exit - trade.entry) / abs(trade.entry - trade.stop)
            assert trade.exit_reason.startswith("SL_GAP"), where
            assert replay_r == pytest.approx(real_r, abs=0.3), f"{where}: {replay_r:.2f}R vs {real_r:.2f}R"
        else:
            assert trade.exit_reason == row["close_reason"], where
            assert trade.exit == pytest.approx(real_exit, abs=ENTRY_TOLERANCE[symbol]), where

        real_costs = float(row["swap_plus_commission"])
        if real_costs:
            # Per lot: the replay sizes on $50,000, the real account held ~$4,900.
            replay_per_lot = (trade.swap_usd + trade.commission_usd) / trade.volume
            real_per_lot = real_costs / float(row["volume"])
            assert replay_per_lot == pytest.approx(real_per_lot, rel=0.05, abs=0.5), where


@pytest.mark.skipif(not DATA_PRESENT, reason="needs refreshed data/history/fundingpips")
def test_the_replay_takes_the_same_paper_setups(replayed) -> None:
    by_config = {
        ("NDX100", "30m/M5/4R"): _config("NDX100", "breakout", 5, 30, 4.0),
        ("XAUUSD", "15m/M1/4R"): _config("XAUUSD", "breakout", 1, 15, 4.0),
        ("XAUUSD", "60m/M1/3R"): _config("XAUUSD", "breakout", 1, 60, 3.0),
    }
    for row in _rows("paper_trades_2026_09.csv"):
        config = by_config[(row["symbol"], row["config"])]
        fill = datetime.fromisoformat(row["fill_time_utc"])
        if not _in_window(fill.astimezone(BROKER_TZ)):
            continue
        match = [t for t in _replayed_for(replayed, config) if t.setup_id == row["setup_id"]]
        assert match, f"{row['bot']} {row['setup_id']} was not taken by the replay"
        trade = match[0]
        assert abs((trade.entry_time - fill).total_seconds()) <= 120
        assert trade.stop == pytest.approx(float(row["sl"]), abs=0.011)
        assert trade.target == pytest.approx(float(row["tp"]), abs=0.011)
