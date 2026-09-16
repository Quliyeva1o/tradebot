# Live Replay Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replay all ten deployed ORB bot configurations over their full history exactly as the VPS runs them — same strategy classes, same 2-minute poll clock, real ask/bid fills, broker-side stops, gap fills, swap — and report the result beside the old batch backtests.

**Architecture:** A new `backtest/live_replay/` package. `configs.py` reads the launchers; `market.py` loads FundingPips M1 bid bars and builds MT5-style M5/M15; `signals.py` wraps the real strategy classes in the runners' own signal rules; `pricing.py` prices entries, exits (via the existing `execution/level_fill.py`), swap and lot size (via the real `PositionSizer`); `ticks.py` serves cached MT5 ticks for session-open gap stops; `engine.py` walks M1 bars, polls every 2 minutes and produces `TradeRecord`s; `metrics.py` and `scripts/live_replay_backtest.py` turn those into the report. Nothing in the live code path is modified.

**Tech Stack:** Python 3.13 (`.venv`), pandas 3.0.3, numpy, pytest 8, MetaTrader5 (read-only, for tick fetching and the symbol snapshot only).

**Spec:** `docs/superpowers/specs/2026-09-15-live-replay-backtest-design.md` (Azerbaijani; it is the contract — read it before Task 1).

## Global Constraints

- Branch `live-replay-backtest`. **Never merge to main and never push** — the VPS pulls main. The working tree is shared with other sessions: `git add` only the exact paths each task names.
- Do not modify `run_live_*.py`, `strategy/*`, `execution/*` behaviour. The one permitted edit outside the new package is adding two logger names to `tests/conftest.py` (Task 6).
- Run tests with `.venv/Scripts/python.exe -m pytest <path> -q`. Baseline: the whole suite passes except `tests/test_nasdaq_midline_sweep_regression.py::test_midline_sweep_ustec_oos_regression` (asserts `108 == 106`), which already fails on untouched `main` — not ours, leave it.
- Bars: CSV `time` is broker server clock (`Europe/Bucharest`); `Bar.timestamp` is genuine UTC; MT5's own epochs (rates *and* ticks) read as server wall-clock and must be re-labelled (see `mt5/rates.py` BROKER_TZ comment).
- pandas 3.0.3 returns `datetime64[us]`; never do integer time math with nanosecond constants. Use `// pd.Timedelta(seconds=1)`.
- Money: start balance $50,000, risk 0.5% (from the launcher), results primarily in R.
- Data lives in `data/history/fundingpips/` (gitignored, stays in the main checkout). Tick cache under `data/history/fundingpips/ticks/` — already covered by `.gitignore`'s `data/**/*.csv`.
- Every commit message ends with:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`

---

### Task 1: Bot configurations from the launchers

**Files:**
- Create: `backtest/live_replay/__init__.py`, `backtest/live_replay/configs.py`
- Create: `tests/live_replay/__init__.py`, `tests/live_replay/test_configs.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `BotConfig(task, family, symbol, paper, risk_pct, scan_minutes, or_minutes, tp_r, entry_window_end)` with `.key`; `parse_bat(path) -> BotConfig`; `load_roster(repo) -> set[str]`; `scope(repo) -> list[BotConfig]`; `REPO`.

- [ ] **Step 1: Write the failing test**

`tests/live_replay/__init__.py` is empty. `tests/live_replay/test_configs.py`:

```python
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


def test_the_roster_lists_the_six_demo_bots_that_may_trade() -> None:
    assert load_roster(REPO) == {
        "OrbBreakout_XAUUSD_Demo", "OrbBreakout_NDX100_Demo", "OrbBreakout_SPX500_Demo",
        "OrbBreakout_DJI30_Demo", "OrbSweep_GER40_Demo", "OrbBreakout_JP225_Demo",
    }


def test_scope_is_the_ten_distinct_configurations_the_repo_deploys() -> None:
    assert {c.task for c in scope(REPO)} == {
        "OrbBreakout_XAUUSD_Demo", "OrbBreakout_NDX100_Demo", "OrbBreakout_SPX500_Demo",
        "OrbBreakout_DJI30_Demo", "OrbBreakout_JP225_Demo", "OrbSweep_GER40_Demo",
        "OrbBreakout_XAUUSD_Paper", "OrbBreakout_GER40_Paper", "OrbSweep_XAUUSD_Paper",
        "OrbSweep_JP225_Paper",
    }
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_configs.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'backtest.live_replay'`.

- [ ] **Step 3: Write the implementation**

`backtest/live_replay/__init__.py`:

```python
"""Replays a deployed bot the way the VPS runs it, rather than the way a batch backtest does.

The batch scripts in scripts/ implement the strategies a second time and agree with the live
classes on only 58-76% of trading days. This package drives the live classes themselves
through the runners' poll clock and a broker that fills at ask/bid, holds the stop, gaps and
charges swap. See docs/superpowers/specs/2026-09-15-live-replay-backtest-design.md.
"""
```

`backtest/live_replay/configs.py`:

```python
"""The bot configurations, read from the launchers the VPS executes.

Parameters are parsed from run_live_orb_*.bat and deploy/demo_roster.txt at run time, never
copied, so the replay always describes what is deployed. Defaults mirror the runners' own
argparse defaults (run_live_nasdaq_orb.py: --scan-timeframe M1, --or-minutes 15;
run_live_xauusd_orb.py: --timeframe M15, --entry-window-end unset).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15}


@dataclass(frozen=True)
class BotConfig:
    """One bot: which strategy, on which symbol, with which parameters."""

    task: str            # Scheduled Task name, e.g. OrbBreakout_NDX100_Demo
    family: str          # "breakout" | "sweep"
    symbol: str
    paper: bool
    risk_pct: float
    scan_minutes: int    # bar size the strategy is fed
    or_minutes: int | None       # breakout only
    tp_r: float | None           # breakout only; the sweep uses its class default (2.0)
    entry_window_end: str | None  # sweep only, "HH:MM" when a launcher overrides the class default

    @property
    def key(self) -> tuple:
        """What makes two launchers the same strategy, ignoring Demo/Paper."""
        return (self.family, self.symbol, self.scan_minutes, self.or_minutes, self.tp_r,
                self.entry_window_end)


def _flag(text: str, name: str, default: str | None = None) -> str | None:
    match = re.search(rf"--{name}\s+(\S+)", text)
    return match.group(1) if match else default


def parse_bat(path: Path) -> BotConfig:
    """Reads one run_live_orb_*.bat into a BotConfig."""
    text = path.read_text(encoding="utf-8")
    family, symbol_tag, mode = path.stem.removeprefix("run_live_orb_").split("_")
    symbol, risk = _flag(text, "symbol"), _flag(text, "risk-per-trade-pct")
    if symbol is None or risk is None:
        raise ValueError(f"{path.name}: --symbol and --risk-per-trade-pct are required")
    common = dict(task=f"Orb{family.capitalize()}_{symbol_tag.upper()}_{mode.capitalize()}",
                  family=family, symbol=symbol, paper="--paper" in text, risk_pct=float(risk))
    if family == "breakout":
        tp_r = _flag(text, "tp-r")
        if tp_r is None:
            raise ValueError(f"{path.name}: --tp-r is required by run_live_nasdaq_orb.py")
        return BotConfig(**common, scan_minutes=_TF_MINUTES[_flag(text, "scan-timeframe", "M1")],
                         or_minutes=int(_flag(text, "or-minutes", "15")), tp_r=float(tp_r),
                         entry_window_end=None)
    if family == "sweep":
        return BotConfig(**common, scan_minutes=_TF_MINUTES[_flag(text, "timeframe", "M15")],
                         or_minutes=None, tp_r=None, entry_window_end=_flag(text, "entry-window-end"))
    raise ValueError(f"{path.name}: unknown strategy family {family!r}")


def load_roster(repo: Path = REPO) -> set[str]:
    """The Demo task names deploy/demo_roster.txt allows to place real orders."""
    lines = (repo / "deploy" / "demo_roster.txt").read_text(encoding="utf-8").splitlines()
    return {parts[0] for parts in (line.split("#", 1)[0].split() for line in lines) if parts}


def scope(repo: Path = REPO) -> list[BotConfig]:
    """Every deployed Demo bot, plus each Paper config that is not one of them."""
    configs = [parse_bat(p) for p in sorted(repo.glob("run_live_orb_*.bat"))]
    roster = load_roster(repo)
    demo = [c for c in configs if not c.paper and c.task in roster]
    demo_keys = {c.key for c in demo}
    paper = [c for c in configs if c.paper and c.key not in demo_keys]
    return demo + paper
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_configs.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest/live_replay/__init__.py backtest/live_replay/configs.py tests/live_replay/__init__.py tests/live_replay/test_configs.py
git commit -m "Read the replayed bot configurations from the launchers themselves"
```

---

### Task 2: Symbol specifications snapshot

**Files:**
- Create: `backtest/live_replay/specs.py`, `backtest/live_replay/symbol_specs.json` (written by the script), `scripts/capture_symbol_specs.py`
- Create: `tests/live_replay/_fixtures.py`, `tests/live_replay/test_specs.py`

**Interfaces:**
- Consumes: `core.models.SymbolConstraints`.
- Produces: `SymbolSpec` with `constraints(usd_per_unit) -> SymbolConstraints`, `usd_per_price_unit(volume, usd_per_unit) -> float`, `margin_usd(volume, price, usd_per_unit) -> float`; `load_specs(path=SPECS_FILE) -> dict[str, SymbolSpec]`; `SPECS_FILE`. `tests/live_replay/_fixtures.py` exports `NDX` and `XAU` specs used by later tasks' tests.

- [ ] **Step 1: Write the failing test**

`tests/live_replay/_fixtures.py`:

```python
"""Symbol specs used across the replay tests -- the real 2026-09-15 MT5 values."""

from backtest.live_replay.specs import SymbolSpec

NDX = SymbolSpec(symbol="NDX100", point=0.01, tick_size=0.01, contract_size=20.0, volume_min=0.01,
                 volume_step=0.01, volume_max=100.0, profit_currency="USD", swap_mode=5,
                 swap_long=-7.33, swap_short=-1.33, swap_rollover3days=5, margin_rate=0.01,
                 commission_per_lot_usd=0.0)

XAU = SymbolSpec(symbol="XAUUSD", point=0.01, tick_size=0.01, contract_size=100.0, volume_min=0.01,
                 volume_step=0.01, volume_max=100.0, profit_currency="USD", swap_mode=1,
                 swap_long=-67.986, swap_short=25.026, swap_rollover3days=3, margin_rate=0.01,
                 commission_per_lot_usd=5.0)
```

`tests/live_replay/test_specs.py`:

```python
"""The contract/lot/swap facts the replay prices everything with."""

from dataclasses import replace

import pytest

from backtest.live_replay.specs import load_specs
from tests.live_replay._fixtures import NDX, XAU


def test_one_tick_is_worth_what_the_connector_derives() -> None:
    # mt5/connector.py's _tick_value returned 0.2 for NDX100 on 2026-09-15.
    assert NDX.constraints(1.0).tick_value == pytest.approx(0.2)


def test_a_yen_contract_is_converted_to_dollars() -> None:
    jp = replace(NDX, symbol="JP225", contract_size=10.0, profit_currency="JPY")
    assert jp.usd_per_price_unit(1.0, 1 / 154.8) == pytest.approx(0.0646, abs=1e-4)


def test_one_point_on_a_tenth_of_a_lot_is_two_dollars_on_ndx100() -> None:
    assert NDX.usd_per_price_unit(0.1, 1.0) == pytest.approx(2.0)


def test_margin_scales_with_notional() -> None:
    assert NDX.margin_usd(0.5, 29000.0, 1.0) == pytest.approx(0.5 * 20 * 29000 * 0.01)


def test_the_committed_snapshot_covers_every_replayed_symbol() -> None:
    specs = load_specs()
    assert set(specs) >= {"XAUUSD", "NDX100", "SPX500", "DJI30", "GER40", "JP225"}
    assert specs["XAUUSD"].swap_mode == 1, "gold swaps in points"
    assert specs["NDX100"].swap_mode == 5, "indices swap as an annual percentage"
    assert specs["XAUUSD"].commission_per_lot_usd == 5.0
    assert specs["GER40"].profit_currency == "EUR" and specs["JP225"].profit_currency == "JPY"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_specs.py -q`
Expected: `ModuleNotFoundError: No module named 'backtest.live_replay.specs'`.

- [ ] **Step 3: Write the implementation**

`backtest/live_replay/specs.py`:

```python
"""Contract, lot, swap and margin facts for each replayed symbol, as MT5 reported them.

A snapshot rather than a live query: a replay must be reproducible without the terminal, and
these change rarely. scripts/capture_symbol_specs.py rewrites symbol_specs.json.

Historical swap rates are not published by the broker, so today's rates are applied to the
whole history -- a stated limitation of the replay, not an oversight.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.models import SymbolConstraints

SPECS_FILE = Path(__file__).with_name("symbol_specs.json")


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    point: float
    tick_size: float
    contract_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    profit_currency: str
    swap_mode: int            # 1 = points per lot per day, 5 = annual percent of the price
    swap_long: float
    swap_short: float
    swap_rollover3days: int   # MT5 weekday numbering: 0 = Sunday, 3 = Wednesday, 5 = Friday
    margin_rate: float        # margin / notional, in account currency
    commission_per_lot_usd: float

    def constraints(self, usd_per_unit: float) -> SymbolConstraints:
        """What PositionSizer needs, with the tick value in USD at this FX rate."""
        return SymbolConstraints(
            symbol=self.symbol, contract_size=self.contract_size, tick_size=self.tick_size,
            tick_value=self.contract_size * self.tick_size * usd_per_unit,
            volume_min=self.volume_min, volume_max=self.volume_max, volume_step=self.volume_step,
        )

    def usd_per_price_unit(self, volume: float, usd_per_unit: float) -> float:
        """Dollars one whole price unit is worth on `volume` lots."""
        return volume * self.contract_size * usd_per_unit

    def margin_usd(self, volume: float, price: float, usd_per_unit: float) -> float:
        return volume * self.contract_size * price * usd_per_unit * self.margin_rate


def load_specs(path: Path = SPECS_FILE) -> dict[str, SymbolSpec]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    fields = set(SymbolSpec.__dataclass_fields__) - {"symbol"}
    return {symbol: SymbolSpec(symbol=symbol, **{k: v for k, v in row.items() if k in fields})
            for symbol, row in raw["symbols"].items()}
```

`scripts/capture_symbol_specs.py`:

```python
"""Snapshot the replayed symbols' contract, lot, swap and margin facts from MT5 (READ-ONLY).

Never places or modifies anything: symbol_info(), order_calc_profit() and order_calc_margin()
only. Run it on the workstation with the terminal logged into FundingPips-Trial.

Usage:
    python -m scripts.capture_symbol_specs
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

import MetaTrader5 as mt5  # noqa: N813

from backtest.live_replay.specs import SPECS_FILE
from mt5.connector import MT5Connector

SYMBOLS = ("XAUUSD", "NDX100", "SPX500", "DJI30", "GER40", "JP225")
# Not in symbol_info: taken from the account's own deals. Deal 12611958 (2026-09-14) charged
# -0.05 on 0.01 XAUUSD lots; no index deal in 2026-09 carried a commission.
COMMISSION_PER_LOT_USD = {"XAUUSD": 5.0}


def capture(symbol: str) -> dict:
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"{symbol} is not available: {mt5.last_error()}")
    info, tick = mt5.symbol_info(symbol), mt5.symbol_info_tick(symbol)
    price = tick.ask
    # Over 1% of price, not one tick: order_calc_profit rounds to account-currency cents and
    # one JP225 tick is worth $0.000648 a lot (see mt5/connector.py's _tick_value).
    move = price * 0.01
    profit = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, symbol, 1.0, price, price + move)
    usd_per_unit = profit / (move * info.trade_contract_size)
    margin = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, symbol, 1.0, price)
    return dict(
        point=info.point, tick_size=info.trade_tick_size, contract_size=info.trade_contract_size,
        volume_min=info.volume_min, volume_step=info.volume_step, volume_max=info.volume_max,
        profit_currency=info.currency_profit, swap_mode=info.swap_mode, swap_long=info.swap_long,
        swap_short=info.swap_short, swap_rollover3days=info.swap_rollover3days,
        margin_rate=margin / (info.trade_contract_size * price * usd_per_unit),
        commission_per_lot_usd=COMMISSION_PER_LOT_USD.get(symbol, 0.0),
        price_at_capture=price, usd_per_unit_at_capture=usd_per_unit,
    )


def main() -> None:
    connector = MT5Connector()
    if not connector.connect():
        raise SystemExit("Could not connect to MT5; open the terminal and log in first.")
    try:
        account = mt5.account_info()
        payload = {
            "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "account": account.login, "server": account.server,
            "symbols": {symbol: capture(symbol) for symbol in SYMBOLS},
        }
    finally:
        connector.disconnect()
    SPECS_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {SPECS_FILE}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Capture the snapshot against the live terminal**

Run: `.venv/Scripts/python.exe -m scripts.capture_symbol_specs`
Expected: `wrote ...symbol_specs.json`. Then check the values against what MT5 reported on 2026-09-15 — `XAUUSD` contract 100 / swap_mode 1 / swap_long -67.986 / rollover3days 3; `NDX100` contract 20 / swap_mode 5 / swap_long -7.33 / rollover3days 5; `SPX500` 50; `DJI30` 5; `GER40` 25 and `profit_currency` EUR; `JP225` 10 and JPY. If a value disagrees, STOP and report it — the snapshot is what every later number is priced with.

- [ ] **Step 5: Run the tests and watch them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_specs.py -q`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add backtest/live_replay/specs.py backtest/live_replay/symbol_specs.json scripts/capture_symbol_specs.py tests/live_replay/_fixtures.py tests/live_replay/test_specs.py
git commit -m "Snapshot the symbols' contract, swap and margin facts from MT5"
```

---

### Task 3: Market data — M1 bars, MT5-style higher timeframes, FX

**Files:**
- Create: `backtest/live_replay/market.py`
- Create: `tests/live_replay/test_market.py`

**Interfaces:**
- Consumes: `core.models.Bar`.
- Produces: `BarFrame` (fields `symbol, minutes, ts, open, high, low, close, spread, ts_close`; methods `bar(i)`, `count_closed_by(epoch)`, `__len__`); `load_bars(path, symbol, minutes, start=None, end=None)`; `load_m1(symbol, data_dir, start=None, end=None)`; `aggregate(m1, minutes)`; `frame_from_bars(symbol, minutes, bars)`; `FxSeries.usd_per_unit(epoch)`; `load_fx(currency, data_dir)`; constants `BROKER_TZ`, `NY`, `DEFAULT_DATA_DIR`.

- [ ] **Step 1: Write the failing test**

`tests/live_replay/test_market.py`:

```python
"""Bars the replay trades on: server-clock CSVs in, UTC bars and MT5-style M15 out."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backtest.live_replay.market import aggregate, load_fx, load_m1


def _csv(tmp_path: Path, name: str, rows: list[tuple[str, float, float, float, float, float]]) -> Path:
    path = tmp_path / name
    lines = ["time,open,high,low,close,volume,spread"]
    lines += [f"{t},{o},{h},{low},{c},1.0,{s}" for t, o, h, low, c, s in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


def test_summer_server_time_is_three_hours_ahead_of_utc(tmp_path: Path) -> None:
    data = _csv(tmp_path, "TEST_M1.csv", [("2026-09-14 17:10:00", 100.0, 101.0, 99.0, 100.5, 3.0)])
    bar = load_m1("TEST", data).bar(0)
    assert bar.timestamp == datetime(2026, 9, 14, 14, 10, tzinfo=UTC)
    assert bar.spread == 3.0


def test_winter_server_time_is_two_hours_ahead_of_utc(tmp_path: Path) -> None:
    data = _csv(tmp_path, "TEST_M1.csv", [("2026-01-15 16:30:00", 1.0, 1.0, 1.0, 1.0, 0.0)])
    assert load_m1("TEST", data).bar(0).timestamp == datetime(2026, 1, 15, 14, 30, tzinfo=UTC)


def test_a_bar_is_visible_only_once_it_has_closed(tmp_path: Path) -> None:
    data = _csv(tmp_path, "TEST_M1.csv", [("2026-09-14 17:10:00", 1.0, 1.0, 1.0, 1.0, 0.0),
                                          ("2026-09-14 17:11:00", 1.0, 1.0, 1.0, 1.0, 0.0)])
    frame = load_m1("TEST", data)
    opened = int(datetime(2026, 9, 14, 14, 11, tzinfo=UTC).timestamp())
    assert frame.count_closed_by(opened) == 1
    assert frame.count_closed_by(opened + 59) == 1
    assert frame.count_closed_by(opened + 60) == 2


def test_m15_bars_are_labelled_by_their_open_and_aggregate_ohlc(tmp_path: Path) -> None:
    rows = [(f"2026-09-14 16:{m:02d}:00", 100.0 + m, 101.0 + m, 99.0 + m, 100.5 + m, 1.0)
            for m in range(30, 48)]
    frame = aggregate(load_m1("TEST", _csv(tmp_path, "TEST_M1.csv", rows)), 15)
    first = frame.bar(0)
    assert len(frame) == 2
    assert first.timestamp == datetime(2026, 9, 14, 13, 30, tzinfo=UTC)
    assert (first.open, first.high, first.low, first.close) == (130.0, 145.0, 129.0, 144.5)
    assert frame.ts_close[0] - frame.ts[0] == 900
    assert (frame.bar(1).open, frame.bar(1).close) == (145.0, 147.5)


def test_a_date_window_trims_the_frame(tmp_path: Path) -> None:
    rows = [("2026-09-13 17:10:00", 1.0, 1.0, 1.0, 1.0, 0.0),
            ("2026-09-14 17:10:00", 2.0, 2.0, 2.0, 2.0, 0.0)]
    frame = load_m1("TEST", _csv(tmp_path, "TEST_M1.csv", rows),
                    start=datetime(2026, 9, 14, tzinfo=UTC))
    assert len(frame) == 1 and frame.bar(0).open == 2.0


def test_fx_uses_the_previous_days_close_so_nothing_looks_ahead(tmp_path: Path) -> None:
    _csv(tmp_path, "USDJPY_D1.csv", [("2026-09-10 00:00:00", 150.0, 150.0, 150.0, 150.0, 0.0),
                                     ("2026-09-11 00:00:00", 155.0, 155.0, 155.0, 155.0, 0.0)])
    fx = load_fx("JPY", tmp_path)
    during_the_eleventh = int(datetime(2026, 9, 11, 12, tzinfo=UTC).timestamp())
    assert fx.usd_per_unit(during_the_eleventh) == pytest.approx(1 / 150)


def test_dollar_symbols_need_no_fx_file(tmp_path: Path) -> None:
    assert load_fx("USD", tmp_path).usd_per_unit(0) == 1.0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_market.py -q`
Expected: `ModuleNotFoundError: No module named 'backtest.live_replay.market'`.

- [ ] **Step 3: Write the implementation**

`backtest/live_replay/market.py`:

```python
"""Bars and FX rates the replay trades on.

The history CSVs hold BID bars whose "time" column is broker server clock (Europe/Bucharest,
see data/download_history.py's write_bars_csv) and whose "spread" column is already in price
units (mt5/rates.py multiplies MT5's integer points by the symbol's point size). Everything
here works in genuine UTC epoch seconds, which is what Bar.timestamp carries live.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from core.models import Bar

BROKER_TZ = ZoneInfo("Europe/Bucharest")
NY = ZoneInfo("America/New_York")
DEFAULT_DATA_DIR = Path("data/history/fundingpips")


@dataclass
class BarFrame:
    """Bid OHLC as arrays; `ts` is each bar's OPEN in UTC epoch seconds."""

    symbol: str
    minutes: int
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    spread: np.ndarray
    ts_close: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self.ts_close = self.ts + self.minutes * 60

    def __len__(self) -> int:
        return int(len(self.ts))

    def bar(self, i: int) -> Bar:
        return Bar(timestamp=datetime.fromtimestamp(int(self.ts[i]), UTC), open=float(self.open[i]),
                   high=float(self.high[i]), low=float(self.low[i]), close=float(self.close[i]),
                   volume=0.0, spread=float(self.spread[i]))

    def count_closed_by(self, epoch: int) -> int:
        """How many bars have closed at or before `epoch` -- a bar is invisible until it closes."""
        return int(np.searchsorted(self.ts_close, epoch, side="right"))


def load_bars(path: Path, symbol: str, minutes: int, start: datetime | None = None,
              end: datetime | None = None) -> BarFrame:
    """Reads one history CSV into a BarFrame."""
    df = pd.read_csv(path)
    naive = pd.to_datetime(df["time"], format="%Y-%m-%d %H:%M:%S")
    # The autumn hour that occurs twice resolves to the first (summer-time) reading, matching
    # scripts/backtest_common.load_m1's datetime.replace(tzinfo=...) (fold=0).
    local = naive.dt.tz_localize(BROKER_TZ, ambiguous=np.ones(len(df), dtype=bool),
                                 nonexistent="shift_forward")
    # pandas 3 keeps microsecond units; dividing Timedeltas avoids any unit assumption.
    epoch = ((local - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)).to_numpy(dtype=np.int64)
    order = np.argsort(epoch, kind="stable")
    epoch = epoch[order]
    keep = np.r_[True, epoch[1:] != epoch[:-1]]
    mask = keep
    if start is not None:
        mask &= epoch >= int(start.timestamp())
    if end is not None:
        mask &= epoch < int(end.timestamp())
    values = {col: df[col].to_numpy(dtype=float)[order][mask] for col in ("open", "high", "low", "close")}
    spread = df["spread"].fillna(0.0).to_numpy(dtype=float)[order][mask] if "spread" in df else np.zeros(mask.sum())
    return BarFrame(symbol=symbol, minutes=minutes, ts=epoch[mask], spread=spread, **values)


def load_m1(symbol: str, data_dir: Path = DEFAULT_DATA_DIR, start: datetime | None = None,
            end: datetime | None = None) -> BarFrame:
    return load_bars(Path(data_dir) / f"{symbol}_M1.csv", symbol, 1, start, end)


def aggregate(m1: BarFrame, minutes: int) -> BarFrame:
    """Builds M5/M15 the way MT5 does: clock-aligned buckets labelled by their open."""
    if minutes == m1.minutes:
        return m1
    bucket = m1.ts - m1.ts % (minutes * 60)
    starts = np.flatnonzero(np.r_[True, bucket[1:] != bucket[:-1]])
    ends = np.r_[starts[1:], len(bucket)] - 1
    return BarFrame(symbol=m1.symbol, minutes=minutes, ts=bucket[starts], open=m1.open[starts],
                    high=np.maximum.reduceat(m1.high, starts), low=np.minimum.reduceat(m1.low, starts),
                    close=m1.close[ends], spread=m1.spread[starts])


def frame_from_bars(symbol: str, minutes: int, bars: Sequence[Bar]) -> BarFrame:
    """A BarFrame from Bar objects -- for tests and for feeding hand-built sessions."""
    return BarFrame(symbol=symbol, minutes=minutes,
                    ts=np.array([int(b.timestamp.timestamp()) for b in bars], dtype=np.int64),
                    open=np.array([b.open for b in bars], dtype=float),
                    high=np.array([b.high for b in bars], dtype=float),
                    low=np.array([b.low for b in bars], dtype=float),
                    close=np.array([b.close for b in bars], dtype=float),
                    spread=np.array([b.spread for b in bars], dtype=float))


@dataclass(frozen=True)
class FxSeries:
    """USD value of one unit of a symbol's profit currency, from daily closes."""

    currency: str
    ts: np.ndarray
    usd: np.ndarray

    def usd_per_unit(self, epoch: int) -> float:
        if self.currency == "USD":
            return 1.0
        # -2: the bar containing `epoch` has not closed yet, so use the previous day's close.
        i = max(int(np.searchsorted(self.ts, epoch, side="right")) - 2, 0)
        return float(self.usd[i])


def load_fx(currency: str, data_dir: Path = DEFAULT_DATA_DIR) -> FxSeries:
    if currency == "USD":
        return FxSeries("USD", np.array([0], dtype=np.int64), np.array([1.0]))
    pair, invert = {"EUR": ("EURUSD", False), "JPY": ("USDJPY", True)}[currency]
    path = Path(data_dir) / f"{pair}_D1.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing; run: python -m data.download_history --symbols {pair} "
            f"--timeframe D1 --start 2020-01-01 --output-dir {data_dir}"
        )
    frame = load_bars(path, pair, 1440)
    return FxSeries(currency, frame.ts, 1.0 / frame.close if invert else frame.close)
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_market.py -q`
Expected: 7 passed.

- [ ] **Step 5: Refresh the real data (needs the MT5 terminal on FundingPips-Trial)**

```bash
.venv/Scripts/python.exe -m data.update_history --symbols XAUUSD,NDX100,SPX500,DJI30,GER40,JP225 --timeframe M1 --overlap-days 3 --output-dir data/history/fundingpips
```

```bash
.venv/Scripts/python.exe -m data.download_history --symbols EURUSD,USDJPY --timeframe D1 --start 2020-01-01 --output-dir data/history/fundingpips
```

Then confirm each M1 file now ends on 2026-09-15 and the two FX files exist:

```bash
for f in XAUUSD NDX100 SPX500 DJI30 GER40 JP225 EURUSD USDJPY; do ls -la data/history/fundingpips/${f}_*.csv 2>/dev/null | tail -1; done
```

Expected: every M1 tail row is dated `2026-09-15`; `EURUSD_D1.csv` and `USDJPY_D1.csv` exist. Data is gitignored — nothing to commit here.

- [ ] **Step 6: Commit**

```bash
git add backtest/live_replay/market.py tests/live_replay/test_market.py
git commit -m "Load the history CSVs as UTC bid bars and build MT5-style M15"
```

---

### Task 4: Pricing — entry, exit, swap, lot size

**Files:**
- Create: `backtest/live_replay/pricing.py`
- Create: `tests/live_replay/test_pricing.py`

**Interfaces:**
- Consumes: `execution.level_fill.exit_fill`, `execution.position_sizer.PositionSizer`, `strategy.risk_reward.resolve_entry_price/resolve_stop_and_target`, `SymbolSpec` (Task 2).
- Produces: `entry_price(direction, bar, spread)`, `ask_bar(bar, spread)`, `exit_on_bar(direction, stop, target, bar, spread, *, entry_bar, after_break) -> tuple[float, str] | None`, `rollover_days(held_from, held_to, triple_weekday) -> int`, `swap_usd(spec, direction, volume, price, days, usd_per_unit) -> float`, `size_position(spec, setup, balance, usd_per_unit, risk_pct) -> float`, `MAX_MARGIN_PCT`.

- [ ] **Step 1: Write the failing test**

`tests/live_replay/test_pricing.py`:

```python
"""How the broker prices a market entry, a stop/target exit, swap and lot size."""

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from backtest.live_replay.pricing import (
    entry_price, exit_on_bar, rollover_days, size_position, swap_usd,
)
from core.models import Bar, SignalDirection, Timeframe
from strategy.models import TradeSetup
from tests.live_replay._fixtures import NDX, XAU


def _bar(o: float, h: float, low: float, c: float) -> Bar:
    return Bar(timestamp=datetime(2026, 9, 14, 14, 31, tzinfo=UTC), open=o, high=h, low=low,
               close=c, volume=1.0)


def _setup(entry: float, stop: float, target: float,
           direction: SignalDirection = SignalDirection.BUY) -> TradeSetup:
    return TradeSetup(setup_id="s", symbol="NDX100", timeframe=Timeframe.M5, direction=direction,
                      entry_zone=(entry, entry), stop_zone=(stop, stop), target_zone=(target, target),
                      confidence_score=1.0, confluence=[], trigger_reason="", invalidations=[],
                      related_structure_break=None, related_order_block=None, related_fvg=None,
                      timestamp=datetime(2026, 9, 14, 14, 5, tzinfo=UTC))


def test_a_buy_pays_the_ask_and_a_sell_gets_the_bid() -> None:
    bar = _bar(100.0, 101.0, 99.0, 100.5)
    assert entry_price(SignalDirection.BUY, bar, 3.0) == 103.0
    assert entry_price(SignalDirection.SELL, bar, 3.0) == 100.0


def test_a_short_stop_triggers_on_the_ask_even_when_the_bid_never_reaches_it() -> None:
    # bid high 199 stays under the 200 stop; the ask (+2) crosses it
    hit = exit_on_bar(SignalDirection.SELL, 200.0, 150.0, _bar(195.0, 199.0, 194.0, 196.0), 2.0,
                      entry_bar=False, after_break=False)
    assert hit == (200.0, "SL")


def test_a_long_stop_ignores_the_spread() -> None:
    assert exit_on_bar(SignalDirection.BUY, 90.0, 150.0, _bar(95.0, 96.0, 90.5, 91.0), 2.0,
                       entry_bar=False, after_break=False) is None


def test_a_stop_after_a_trading_break_fills_at_the_close_when_that_is_worse() -> None:
    assert exit_on_bar(SignalDirection.BUY, 90.0, 150.0, _bar(95.0, 95.0, 80.0, 82.0), 0.0,
                       entry_bar=False, after_break=True) == (82.0, "SL")


def test_index_swap_days_are_one_per_weeknight_and_three_on_friday() -> None:
    assert rollover_days(date(2026, 9, 10), date(2026, 9, 11), 5) == 1
    assert rollover_days(date(2026, 9, 11), date(2026, 9, 14), 5) == 3


def test_gold_triples_on_wednesday_not_friday() -> None:
    assert rollover_days(date(2026, 9, 11), date(2026, 9, 14), 3) == 1
    assert rollover_days(date(2026, 9, 9), date(2026, 9, 10), 3) == 3


def test_one_night_of_index_swap_matches_the_real_deal() -> None:
    # deal 12377152: 0.01 NDX100 lots, Thursday to Friday, swap -1.19
    assert swap_usd(NDX, SignalDirection.BUY, 0.01, 29210.38, 1, 1.0) == pytest.approx(-1.19, abs=0.01)


def test_a_weekend_of_index_swap_matches_the_real_deal() -> None:
    # deal 12494940: 0.01 NDX100 lots held Friday to Monday, swap -3.59
    assert swap_usd(NDX, SignalDirection.BUY, 0.01, 29466.30, 3, 1.0) == pytest.approx(-3.59, abs=0.02)


def test_gold_swap_in_points_matches_the_real_deal() -> None:
    # deal 12048249: 0.06 XAUUSD lots, one night, -4.08
    assert swap_usd(XAU, SignalDirection.BUY, 0.06, 4408.17, 1, 1.0) == pytest.approx(-4.08, abs=0.01)


def test_size_risks_half_a_percent_of_the_balance() -> None:
    # $250 budget; a 197.37-point stop costs $3,947.4 a lot -> 0.0633 -> 0.06 after the step
    assert size_position(NDX, _setup(29048.08, 28850.71, 29837.56), 50_000.0, 1.0, 0.005) == pytest.approx(0.06)


def test_the_margin_ceiling_scales_an_oversized_entry_down() -> None:
    # A 1-point stop buys 12.5 lots, whose margin at 5% is 7x the 20% ceiling on $50k
    volume = size_position(replace(NDX, margin_rate=0.05), _setup(29048.08, 29047.08, 29052.08),
                           50_000.0, 1.0, 0.005)
    assert volume == pytest.approx(0.34)


def test_a_setup_whose_stop_equals_its_entry_cannot_be_sized() -> None:
    assert size_position(NDX, _setup(29048.08, 29048.08, 29100.0), 50_000.0, 1.0, 0.005) == 0.0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_pricing.py -q`
Expected: `ModuleNotFoundError: No module named 'backtest.live_replay.pricing'`.

- [ ] **Step 3: Write the implementation**

`backtest/live_replay/pricing.py`:

```python
"""How the broker prices one bot's entry, exit, swap and lot size.

Exits reuse execution/level_fill.py -- the same rules PaperBroker(level_fills=True) applies --
so a replayed trade and a paper trade are priced identically. That module works on bid bars,
which is right for a long; a short's stop and target trigger on the ask, so the bar is shifted
by the spread before it is priced.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, timedelta

from core.models import Bar, SignalDirection
from execution.level_fill import exit_fill
from execution.position_sizer import PositionSizer
from strategy.models import TradeSetup
from strategy.risk_reward import resolve_entry_price, resolve_stop_and_target

from backtest.live_replay.specs import SymbolSpec

MAX_MARGIN_PCT = 0.20  # execution/trade_manager.py's DEFAULT_MAX_MARGIN_PCT


def entry_price(direction: SignalDirection, bar: Bar, spread: float) -> float:
    """A market order filled at `bar`'s open: a buy pays the ask, a sell gets the bid."""
    return bar.open + spread if direction == SignalDirection.BUY else bar.open


def ask_bar(bar: Bar, spread: float) -> Bar:
    return replace(bar, open=bar.open + spread, high=bar.high + spread, low=bar.low + spread,
                   close=bar.close + spread)


def exit_on_bar(direction: SignalDirection, stop: float, target: float, bar: Bar, spread: float, *,
                entry_bar: bool, after_break: bool) -> tuple[float, str] | None:
    """(price, "SL"|"TP") if this bar closes the trade, priced on the side that triggers."""
    priced = bar if direction == SignalDirection.BUY else ask_bar(bar, spread)
    return exit_fill(direction, stop, target, priced, entry_bar=entry_bar, after_break=after_break)


def rollover_days(held_from: date, held_to: date, triple_weekday: int) -> int:
    """Swap days charged for the server midnights crossed between the two dates.

    One per weeknight, three on `triple_weekday` (MT5 numbering, 0 = Sunday), none over the
    weekend -- the triple night is what pays for it.
    """
    days, day = 0, held_from
    while day < held_to:
        mt5_weekday = (day.weekday() + 1) % 7
        if 1 <= mt5_weekday <= 5:
            days += 3 if mt5_weekday == triple_weekday else 1
        day += timedelta(days=1)
    return days


def swap_usd(spec: SymbolSpec, direction: SignalDirection, volume: float, price: float, days: int,
             usd_per_unit: float) -> float:
    """What the broker charges for holding `volume` lots over `days` rollovers."""
    rate = spec.swap_long if direction == SignalDirection.BUY else spec.swap_short
    if spec.swap_mode == 1:      # points per lot per day (gold here)
        per_day = volume * spec.contract_size * spec.point * rate
    elif spec.swap_mode == 5:    # annual percent of the current price (the indices here)
        per_day = volume * spec.contract_size * price * rate / 100.0 / 360.0
    else:
        raise ValueError(f"{spec.symbol}: swap_mode {spec.swap_mode} is not modelled")
    return per_day * days * usd_per_unit


def size_position(spec: SymbolSpec, setup: TradeSetup, balance: float, usd_per_unit: float,
                  risk_pct: float) -> float:
    """TradeManager.open_trade's sizing: PositionSizer on balance, then the 20% margin ceiling.

    A flat bot's equity is its balance, so the ceiling is measured against that.
    """
    entry = resolve_entry_price(setup)
    stop, _ = resolve_stop_and_target(setup)
    volume = PositionSizer(risk_per_trade_pct=risk_pct).calculate_size(
        balance, entry, stop, spec.constraints(usd_per_unit))
    if volume <= 0:
        return 0.0
    margin = spec.margin_usd(volume, entry, usd_per_unit)
    ceiling = balance * MAX_MARGIN_PCT
    if margin <= ceiling:
        return volume
    capped = round(math.floor(volume * (ceiling / margin) / spec.volume_step) * spec.volume_step, 8)
    return capped if capped >= spec.volume_min else 0.0
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_pricing.py -q`
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest/live_replay/pricing.py tests/live_replay/test_pricing.py
git commit -m "Price replayed fills, swap and lot size the way the broker does"
```

---

### Task 5: Tick cache for session-open gap stops

**Files:**
- Create: `backtest/live_replay/ticks.py`
- Create: `tests/live_replay/test_ticks.py`

**Interfaces:**
- Consumes: `core.models.SignalDirection`; `mt5.connector.MT5Connector` only lazily, on a cache miss. It defines its own `BROKER_TZ` rather than importing `mt5/rates.py`, which loads MetaTrader5 and `.env` at import time.
- Produces: `TickCache(cache_dir, fetch=mt5_fetch)` with `has_history(symbol, epoch) -> bool`, `window(symbol, start, seconds) -> list[TickRow]`, `first_crossing(symbol, start, direction, level, seconds=300) -> float | None`; `mt5_fetch`; `TICK_HISTORY_START`, `DEFAULT_TICK_HISTORY_START`.

- [ ] **Step 1: Write the failing test**

`tests/live_replay/test_ticks.py`:

```python
"""Real ticks decide where a stop filled after a weekend, and are cached so a rerun is offline."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backtest.live_replay.ticks import TickCache
from core.models import SignalDirection

MONDAY_OPEN = int(datetime(2026, 9, 13, 22, 0, tzinfo=UTC).timestamp())


def test_the_stale_weekend_requote_does_not_trigger_the_stop(tmp_path: Path) -> None:
    calls: list[tuple] = []

    def fetch(symbol: str, start: int, end: int) -> list[tuple[int, float, float]]:
        calls.append((symbol, start, end))
        # The first tick of the week re-quotes Friday's close; the real price arrives 6s later.
        return [(start * 1000, 29370.20, 29373.20), (start * 1000 + 6000, 29030.33, 29033.33)]

    cache = TickCache(tmp_path, fetch)
    assert cache.first_crossing("NDX100", MONDAY_OPEN, SignalDirection.BUY, 29326.58) == 29030.33
    assert cache.first_crossing("NDX100", MONDAY_OPEN, SignalDirection.BUY, 29326.58) == 29030.33
    assert len(calls) == 1, "the second call must be served from the cache"


def test_a_short_stop_crosses_on_the_ask(tmp_path: Path) -> None:
    cache = TickCache(tmp_path, lambda s, a, b: [(a * 1000, 100.0, 101.0), (a * 1000 + 1, 100.5, 102.5)])
    assert cache.first_crossing("GER40", MONDAY_OPEN, SignalDirection.SELL, 102.0) == 102.5


def test_a_window_with_no_crossing_tick_returns_nothing(tmp_path: Path) -> None:
    cache = TickCache(tmp_path, lambda s, a, b: [(a * 1000, 100.0, 101.0)])
    assert cache.first_crossing("GER40", MONDAY_OPEN, SignalDirection.BUY, 50.0) is None


def test_nothing_is_fetched_before_the_brokers_tick_history_starts(tmp_path: Path) -> None:
    cache = TickCache(tmp_path, lambda *a: pytest.fail("must not fetch"))
    before = int(datetime(2024, 1, 8, tzinfo=UTC).timestamp())
    assert cache.first_crossing("NDX100", before, SignalDirection.BUY, 1.0) is None


def test_gold_ticks_start_later_than_the_indices(tmp_path: Path) -> None:
    cache = TickCache(tmp_path, lambda *a: pytest.fail("must not fetch"))
    march_2026 = int(datetime(2026, 3, 2, tzinfo=UTC).timestamp())
    assert cache.has_history("NDX100", march_2026) is True
    assert cache.has_history("XAUUSD", march_2026) is False


def test_an_empty_window_is_cached_too(tmp_path: Path) -> None:
    calls: list[int] = []

    def fetch(symbol: str, start: int, end: int) -> list:
        calls.append(start)
        return []

    cache = TickCache(tmp_path, fetch)
    assert cache.window("NDX100", MONDAY_OPEN, 60) == []
    assert cache.window("NDX100", MONDAY_OPEN, 60) == []
    assert len(calls) == 1
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_ticks.py -q`
Expected: `ModuleNotFoundError: No module named 'backtest.live_replay.ticks'`.

- [ ] **Step 3: Write the implementation**

`backtest/live_replay/ticks.py`:

```python
"""Real MT5 ticks around one moment, cached on disk.

Only session-open stops need them: the first tick of a new week is a stale re-quote of the
previous close, and the real price lands seconds later -- which is why 2026-09-14's stops
filled at -3.1R while the bar's own open says -1R. Everything else is priced from bars.

MT5 timestamps (rates AND ticks) read as the broker's wall clock, not UTC -- see the BROKER_TZ
comment in mt5/rates.py. Requests and replies are converted on both sides here.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.models import SignalDirection

BROKER_TZ = ZoneInfo("Europe/Bucharest")
TickRow = tuple[int, float, float]  # (epoch milliseconds UTC, bid, ask)
Fetch = Callable[[str, int, int], list[TickRow]]

# Probed on FundingPips-Trial 2026-09-15: copy_ticks_range returns nothing before these.
DEFAULT_TICK_HISTORY_START = datetime(2025, 3, 14, tzinfo=UTC)
TICK_HISTORY_START = {"XAUUSD": datetime(2026, 5, 5, tzinfo=UTC)}


def _to_broker_clock(epoch: int) -> datetime:
    """The datetime MT5 expects: broker wall clock, labelled UTC."""
    return datetime.fromtimestamp(epoch, BROKER_TZ).replace(tzinfo=UTC)


def _from_broker_clock(msc: int) -> int:
    """MT5's millisecond stamp (broker wall clock) as genuine UTC milliseconds."""
    naive = datetime.fromtimestamp(msc / 1000, UTC).replace(tzinfo=None)
    return int(naive.replace(tzinfo=BROKER_TZ).astimezone(UTC).timestamp() * 1000)


def mt5_fetch(symbol: str, start: int, end: int) -> list[TickRow]:
    """Ticks in [start, end) epoch seconds, from the terminal. Read-only."""
    import MetaTrader5 as mt5  # noqa: N813 -- only needed when the cache misses

    from mt5.connector import MT5Connector

    if mt5.terminal_info() is None and not MT5Connector().connect():
        raise RuntimeError("MT5 is not reachable; cannot fill the tick cache")
    mt5.symbol_select(symbol, True)
    rows = mt5.copy_ticks_range(symbol, _to_broker_clock(start), _to_broker_clock(end),
                                mt5.COPY_TICKS_ALL)
    if rows is None:
        return []
    return [(_from_broker_clock(int(r["time_msc"])), float(r["bid"]), float(r["ask"])) for r in rows]


class TickCache:
    """Serves tick windows from disk, fetching (once) only what is missing."""

    def __init__(self, cache_dir: Path, fetch: Fetch = mt5_fetch) -> None:
        self._dir = Path(cache_dir)
        self._fetch = fetch

    def has_history(self, symbol: str, epoch: int) -> bool:
        start = TICK_HISTORY_START.get(symbol, DEFAULT_TICK_HISTORY_START)
        return epoch >= int(start.timestamp())

    def window(self, symbol: str, start: int, seconds: int) -> list[TickRow]:
        path = self._dir / symbol / f"{start}_{seconds}.csv"
        if path.exists():
            with path.open(newline="", encoding="utf-8") as handle:
                return [(int(r["time_msc"]), float(r["bid"]), float(r["ask"]))
                        for r in csv.DictReader(handle)]
        rows = self._fetch(symbol, start, start + seconds)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["time_msc", "bid", "ask"])
            writer.writerows(rows)
        return rows

    def first_crossing(self, symbol: str, start: int, direction: SignalDirection, level: float,
                       seconds: int = 300) -> float | None:
        """The price of the first tick that reaches `level`, or None when there is no tick history."""
        if not self.has_history(symbol, start):
            return None
        for _, bid, ask in self.window(symbol, start, seconds):
            if direction == SignalDirection.BUY and bid <= level:
                return bid
            if direction == SignalDirection.SELL and ask >= level:
                return ask
        return None
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_ticks.py -q`
Expected: 6 passed.

- [ ] **Step 5: Verify the real conversion against a known fill**

```bash
.venv/Scripts/python.exe -c "from datetime import UTC, datetime; from backtest.live_replay.ticks import TickCache; from core.models import SignalDirection; from pathlib import Path; c = TickCache(Path('data/history/fundingpips/ticks')); start = int(datetime(2026, 9, 13, 22, 0, tzinfo=UTC).timestamp()); print(c.first_crossing('NDX100', start, SignalDirection.BUY, 29326.58))"
```

Expected: a price near **29030.33** — the real fill of deal 12494940 (Monday 2026-09-14 01:00:06 server = 2026-09-13 22:00:06 UTC). A number near 29326 means the broker-clock conversion is inverted; anything else, STOP and debug before continuing.

- [ ] **Step 6: Commit**

```bash
git add backtest/live_replay/ticks.py tests/live_replay/test_ticks.py
git commit -m "Cache the ticks that decide where a session-open stop filled"
```

---

### Task 6: Signals — what the runner would act on at a poll

**Files:**
- Create: `backtest/live_replay/signals.py`
- Create: `tests/live_replay/test_signals.py`
- Modify: `tests/conftest.py` (add two logger names to `_ISOLATED_LOGGER_NAMES`)

**Interfaces:**
- Consumes: `BotConfig`, `BarFrame`, the two strategy classes.
- Produces: `grace_bars(scan_minutes) -> int`; `BreakoutSignals(config, scan)` and `SweepSignals(config, scan, lookback_bars=SWEEP_LOOKBACK_BARS)`, both with `at(n_closed) -> TradeSetup | None`; `make_signals(config, scan)`; `SWEEP_LOOKBACK_BARS`, `SIGNAL_GRACE_MINUTES`.

- [ ] **Step 1: Write the failing test**

`tests/live_replay/test_signals.py`:

```python
"""A poll acts on the newest setup of today, and only while it is inside the grace window."""

from datetime import datetime, timedelta

import pytest

from backtest.live_replay.configs import BotConfig
from backtest.live_replay.market import NY, frame_from_bars
from backtest.live_replay.signals import grace_bars, make_signals
from core.models import Bar

BREAKOUT = BotConfig(task="T", family="breakout", symbol="SYN", paper=False, risk_pct=0.005,
                     scan_minutes=1, or_minutes=15, tp_r=4.0, entry_window_end=None)


def _bar(ts: datetime, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(timestamp=ts.astimezone(NY), open=o, high=h, low=low, close=c, volume=1.0, spread=0.1)


def _orb_day(day: datetime, tail: int = 20) -> list[Bar]:
    """A 09:30-09:44 range of 100-101, a breakout close at 09:52, then a quiet tail."""
    bars = [_bar(day + timedelta(minutes=i), 100.5, 101.0, 100.0, 100.5) for i in range(15)]
    bars += [_bar(day + timedelta(minutes=i), 100.5, 100.9, 100.2, 100.6) for i in range(15, 22)]
    bars.append(_bar(day + timedelta(minutes=22), 100.9, 102.0, 100.9, 101.8))
    bars += [_bar(day + timedelta(minutes=23 + i), 101.5, 101.6, 101.2, 101.4) for i in range(tail)]
    return bars


def test_grace_is_four_minutes_expressed_in_bars() -> None:
    assert (grace_bars(1), grace_bars(5), grace_bars(15)) == (4, 1, 1)


def test_the_breakout_setup_is_actionable_for_four_bars_then_expires() -> None:
    day = datetime(2026, 9, 10, 9, 30, tzinfo=NY)
    signals = make_signals(BREAKOUT, frame_from_bars("SYN", 1, _orb_day(day)))
    breakout_closed = 23  # bars 0..22 have closed once bar 22 is done
    assert signals.at(breakout_closed) is not None
    assert signals.at(breakout_closed + 4) is not None
    assert signals.at(breakout_closed + 5) is None


def test_the_setup_carries_the_opening_ranges_low_as_its_stop_and_a_four_r_target() -> None:
    day = datetime(2026, 9, 10, 9, 30, tzinfo=NY)
    setup = make_signals(BREAKOUT, frame_from_bars("SYN", 1, _orb_day(day))).at(23)
    assert setup.stop_zone == (100.0, 100.0)
    assert setup.entry_zone[0] == pytest.approx(101.8)
    assert setup.target_zone[0] == pytest.approx(101.8 + 4 * 1.8)


def test_yesterdays_setup_is_never_acted_on_today() -> None:
    first = _orb_day(datetime(2026, 9, 10, 9, 30, tzinfo=NY), tail=30)
    second = [_bar(datetime(2026, 9, 11, 9, 30, tzinfo=NY) + timedelta(minutes=i), 100.5, 100.9, 100.2, 100.6)
              for i in range(10)]
    signals = make_signals(BREAKOUT, frame_from_bars("SYN", 1, first + second))
    assert signals.at(len(first) + 10) is None


def test_the_sweep_is_not_replayed_outside_its_entry_window() -> None:
    config = BotConfig(task="S", family="sweep", symbol="SYN", paper=False, risk_pct=0.005,
                       scan_minutes=15, or_minutes=None, tp_r=None, entry_window_end=None)
    morning = datetime(2026, 9, 10, 8, 0, tzinfo=NY)
    bars = [_bar(morning + timedelta(minutes=15 * i), 100.0, 101.0, 99.0, 100.0) for i in range(4)]
    assert make_signals(config, frame_from_bars("SYN", 15, bars)).at(4) is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_signals.py -q`
Expected: `ModuleNotFoundError: No module named 'backtest.live_replay.signals'`.

- [ ] **Step 3: Write the implementation**

`backtest/live_replay/signals.py`:

```python
"""Which setup a runner would act on at a given poll.

Mirrors run_live_nasdaq_orb.py / run_live_xauusd_orb.py's _evaluate_for_new_trade: replay the
fetched bars through the strategy, keep the newest setup, and act on it only while it belongs
to today and is no more than SIGNAL_GRACE_MINUTES old.

The two families need different handling. The breakout class resets its state at every NY date
change and reads only the newest bar, so feeding bars once, in order, is identical to the
runner's per-poll replay of two days (tests/live_replay/test_runner_equivalence.py proves it).
The sweep class seeds a Wilder ATR(14) from whatever window it is fed, and the runner feeds it
three days -- so it must be rebuilt on that same window whenever a bar closes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time

from core.models import Timeframe
from market_structure.structure_models import MarketState
from strategy.models import TradeSetup
from strategy.nasdaq_orb_m1_breakout import NasdaqOrbM1BreakoutConfig, NasdaqOrbM1BreakoutStrategy
from strategy.xauusd_orb_liquidity_sweep import (
    XauusdOrbLiquiditySweepConfig, XauusdOrbLiquiditySweepStrategy,
)

from backtest.live_replay.configs import BotConfig
from backtest.live_replay.market import NY, BarFrame

SIGNAL_GRACE_MINUTES = 4          # both runners
SWEEP_LOOKBACK_BARS = 3 * 96      # run_live_xauusd_orb.py: DEFAULT_LOOKBACK_DAYS x M15 bars a day
_STATE_RESET_BARS = 1024          # the classes read only the newest bar; keep MarketState small


def grace_bars(scan_minutes: int) -> int:
    return max(1, SIGNAL_GRACE_MINUTES // scan_minutes)


def _ny_date(frame: BarFrame, index: int):
    return datetime.fromtimestamp(int(frame.ts[index]), UTC).astimezone(NY).date()


def _actionable(found: tuple[TradeSetup, int] | None, n_closed: int, frame: BarFrame,
                grace: int) -> TradeSetup | None:
    if found is None or n_closed == 0:
        return None
    setup, index = found
    if setup.timestamp.astimezone(NY).date() != _ny_date(frame, n_closed - 1):
        return None
    return setup if n_closed - 1 - index <= grace else None


class BreakoutSignals:
    """One strategy instance, fed every closed scan bar exactly once."""

    def __init__(self, config: BotConfig, scan: BarFrame) -> None:
        self._scan = scan
        self._symbol = config.symbol
        self._timeframe = Timeframe(f"M{config.scan_minutes}")
        self._strategy = NasdaqOrbM1BreakoutStrategy(
            config=NasdaqOrbM1BreakoutConfig(or_minutes=config.or_minutes, tp_r=config.tp_r,
                                             direction="long"))
        self._state = MarketState(symbol=self._symbol, timeframe=self._timeframe)
        self._fed = 0
        self._found: tuple[TradeSetup, int] | None = None
        self._grace = grace_bars(config.scan_minutes)

    def at(self, n_closed: int) -> TradeSetup | None:
        while self._fed < n_closed:
            if self._state.bar_count() >= _STATE_RESET_BARS:
                self._state = MarketState(symbol=self._symbol, timeframe=self._timeframe)
            self._state.append_bar(self._scan.bar(self._fed))
            setup = self._strategy.evaluate(self._state)
            if setup is not None:
                self._found = (setup, self._fed)
            self._fed += 1
        return _actionable(self._found, n_closed, self._scan, self._grace)


class SweepSignals:
    """A fresh strategy over the runner's last three days of bars, rebuilt as bars close."""

    def __init__(self, config: BotConfig, scan: BarFrame,
                 lookback_bars: int = SWEEP_LOOKBACK_BARS) -> None:
        cfg = XauusdOrbLiquiditySweepConfig()
        if config.entry_window_end:
            hour, minute = (int(part) for part in config.entry_window_end.split(":"))
            cfg = replace(cfg, entry_window_end=time(hour, minute))
        self._cfg, self._scan, self._lookback = cfg, scan, lookback_bars
        self._symbol = config.symbol
        self._timeframe = Timeframe(f"M{config.scan_minutes}")
        self._grace = grace_bars(config.scan_minutes)
        self._last_n, self._last = -1, None
        # A setup bar opens after the opening-range candle and before the entry window ends; the
        # newest bar can be that bar or up to `grace` bars later. Outside that, nothing is
        # actionable, so the replay is skipped.
        self._first_minute = _minutes(cfg.or_start) + config.scan_minutes
        self._last_minute = _minutes(cfg.entry_window_end) + self._grace * config.scan_minutes

    def at(self, n_closed: int) -> TradeSetup | None:
        if n_closed != self._last_n:
            self._last_n = n_closed
            self._last = self._replay(n_closed) if self._in_window(n_closed) else None
        return self._last

    def _in_window(self, n_closed: int) -> bool:
        if n_closed == 0:
            return False
        local = datetime.fromtimestamp(int(self._scan.ts[n_closed - 1]), UTC).astimezone(NY)
        return self._first_minute <= local.hour * 60 + local.minute <= self._last_minute

    def _replay(self, n_closed: int) -> TradeSetup | None:
        strategy = XauusdOrbLiquiditySweepStrategy(config=self._cfg)
        state = MarketState(symbol=self._symbol, timeframe=self._timeframe)
        found: tuple[TradeSetup, int] | None = None
        for i in range(max(0, n_closed - self._lookback), n_closed):
            state.append_bar(self._scan.bar(i))
            setup = strategy.evaluate(state)
            if setup is not None:
                found = (setup, i)
        return _actionable(found, n_closed, self._scan, self._grace)


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def make_signals(config: BotConfig, scan: BarFrame) -> BreakoutSignals | SweepSignals:
    return BreakoutSignals(config, scan) if config.family == "breakout" else SweepSignals(config, scan)
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_signals.py -q`
Expected: 5 passed.

- [ ] **Step 5: Stop the runner tests from writing into the real logs**

The next task drives `run_live_nasdaq_orb` / `run_live_xauusd_orb` directly. Their loggers write to `logs/run_live_*.log` — `tests/test_no_reentry_after_stopout.py` already leaks fake fills there (`fill_price=101.90000` lines dated 2026-09-14). In `tests/conftest.py`, extend `_ISOLATED_LOGGER_NAMES`:

```python
    # tests/test_run_live_first_fvg_window.py drives run_once() with fake orders.
    "run_live_first_fvg_window",
    # tests/live_replay/test_runner_equivalence.py and tests/test_no_reentry_after_stopout.py
    # call the ORB runners' own _evaluate_for_new_trade with fake orders.
    "run_live_nasdaq_orb",
    "run_live_xauusd_orb",
```

- [ ] **Step 6: Verify the log no longer grows**

```bash
wc -c logs/run_live_nasdaq_orb.log && .venv/Scripts/python.exe -m pytest tests/test_no_reentry_after_stopout.py -q && wc -c logs/run_live_nasdaq_orb.log
```

Expected: tests pass and the byte count is identical before and after.

- [ ] **Step 7: Commit**

```bash
git add backtest/live_replay/signals.py tests/live_replay/test_signals.py tests/conftest.py
git commit -m "Reproduce the runners' signal rules, and keep their tests out of the real logs"
```

---

### Task 7: Equivalence with the real runners (gate G2)

**Files:**
- Create: `tests/live_replay/test_runner_equivalence.py`

**Interfaces:**
- Consumes: `scope()`, `load_m1`, `aggregate`, `make_signals`, `grace_bars`, `SWEEP_LOOKBACK_BARS`, the two runner modules.
- Produces: nothing — a gate.

- [ ] **Step 1: Write the test**

This one is expected to pass immediately if Task 6 is right; it exists to prove it. `tests/live_replay/test_runner_equivalence.py`:

```python
"""The replay must act on exactly the setup the live runner would, at every poll.

Drives the runners' own _evaluate_for_new_trade over the bars the VPS would have fetched at
each poll (run_once: DEFAULT_LOOKBACK_DAYS x bars a day) and compares with the replay's
signals. Needs data/history/fundingpips; skipped where it is absent.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import run_live_nasdaq_orb
import run_live_xauusd_orb
from backtest.live_replay.configs import scope
from backtest.live_replay.market import DEFAULT_DATA_DIR, NY, aggregate, load_m1
from backtest.live_replay.signals import SWEEP_LOOKBACK_BARS, grace_bars, make_signals
from core.models import Timeframe
from execution.order import OrderStatus
from strategy.nasdaq_orb_m1_breakout import NasdaqOrbM1BreakoutConfig, NasdaqOrbM1BreakoutStrategy
from strategy.xauusd_orb_liquidity_sweep import (
    XauusdOrbLiquiditySweepConfig, XauusdOrbLiquiditySweepStrategy,
)

CONFIGS = scope()
DATA_PRESENT = all((DEFAULT_DATA_DIR / f"{c.symbol}_M1.csv").exists() for c in CONFIGS)
REPLAY_FROM = datetime(2025, 9, 1, tzinfo=UTC)
BREAKOUT_LOOKBACK = {1: 2 * 1440, 5: 2 * 288}  # run_live_nasdaq_orb.run_once
SETUP_DAYS, RANDOM_DAYS = 3, 3


class _Capture:
    """Stands in for TradeManager: fills everything and remembers the setup it was given."""

    def __init__(self) -> None:
        self.setups: list = []
        self.last_open_result = None
        self._position_sizer = None

    def open_trade(self, setup, broker):
        self.setups.append(setup)
        return SimpleNamespace(status=OrderStatus.FILLED, fill_price=setup.entry_zone[0],
                               order_id="replay")


@pytest.fixture(autouse=True)
def _quiet_runners(monkeypatch: pytest.MonkeyPatch) -> None:
    for runner in (run_live_nasdaq_orb, run_live_xauusd_orb):
        monkeypatch.setattr(runner, "_log_trade_event", lambda *_a, **_k: None)
        monkeypatch.setattr(runner, "_log_sizing", lambda *_a, **_k: None)


def _fresh_strategy(config):
    if config.family == "breakout":
        return NasdaqOrbM1BreakoutStrategy(
            config=NasdaqOrbM1BreakoutConfig(or_minutes=config.or_minutes, tp_r=config.tp_r,
                                             direction="long"))
    return XauusdOrbLiquiditySweepStrategy(config=XauusdOrbLiquiditySweepConfig())


def _runner_setup(config, bars, tmp_path):
    runner = run_live_nasdaq_orb if config.family == "breakout" else run_live_xauusd_orb
    capture = _Capture()
    ledger = tmp_path / "ledger.json"
    ledger.unlink(missing_ok=True)
    if bars:
        runner._evaluate_for_new_trade(
            capture, None, _fresh_strategy(config), bars, config.symbol,
            Timeframe(f"M{config.scan_minutes}"), grace_bars(config.scan_minutes),
            kill_switch_flag_path=tmp_path / "no_kill_switch.flag", traded_setups_path=ledger)
    return capture.setups[-1] if capture.setups else None


def _key(setup):
    return None if setup is None else (setup.setup_id, setup.entry_zone, setup.stop_zone,
                                       setup.target_zone)


def _polls(day):
    poll = datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY)
    while poll.hour < 12 or (poll.hour == 12 and poll.minute <= 30):
        yield poll
        poll += timedelta(minutes=2)


@pytest.mark.skipif(not DATA_PRESENT, reason="needs data/history/fundingpips")
@pytest.mark.parametrize("config", CONFIGS, ids=lambda c: c.task)
def test_the_replay_acts_on_the_same_setup_as_the_runner(config, tmp_path) -> None:
    m1 = load_m1(config.symbol, DEFAULT_DATA_DIR, start=REPLAY_FROM)
    scan = aggregate(m1, config.scan_minutes)
    lookback = (BREAKOUT_LOOKBACK[config.scan_minutes] if config.family == "breakout"
                else SWEEP_LOOKBACK_BARS)

    # Days the replay itself finds a setup on, so the comparison is not all None, plus random days.
    scout = make_signals(config, scan)
    setup_days, all_days = [], []
    for i in range(lookback, len(scan)):
        day = datetime.fromtimestamp(int(scan.ts[i]), UTC).astimezone(NY).date()
        if not all_days or all_days[-1] != day:
            all_days.append(day)
        if scout.at(i + 1) is not None and (not setup_days or setup_days[-1] != day):
            setup_days.append(day)
    rng = random.Random(20260915)
    days = sorted(set(rng.sample(setup_days, min(SETUP_DAYS, len(setup_days)))
                      + rng.sample(all_days, RANDOM_DAYS)))
    assert setup_days, f"{config.task}: the replay found no setup at all in the sample window"

    signals = make_signals(config, scan)
    for day in days:
        for poll in _polls(day):
            n_closed = scan.count_closed_by(int(poll.timestamp()))
            bars = [scan.bar(i) for i in range(max(0, n_closed - lookback), n_closed)]
            expected = _runner_setup(config, bars, tmp_path)
            assert _key(signals.at(n_closed)) == _key(expected), (
                f"{config.task} disagrees at {poll:%Y-%m-%d %H:%M} NY")


@pytest.mark.skipif(not DATA_PRESENT, reason="needs data/history/fundingpips")
@pytest.mark.parametrize("symbol", ["NDX100", "GER40", "XAUUSD"])
def test_m15_built_from_m1_matches_the_brokers_own_m15(symbol) -> None:
    native_path = DEFAULT_DATA_DIR / f"{symbol}_M15.csv"
    if not native_path.exists():
        pytest.skip(f"no native {symbol}_M15.csv to compare against")
    from backtest.live_replay.market import load_bars

    start = datetime(2026, 6, 1, tzinfo=UTC)
    built = aggregate(load_m1(symbol, DEFAULT_DATA_DIR, start=start), 15)
    native = load_bars(native_path, symbol, 15, start=start)
    shared = set(built.ts.tolist()) & set(native.ts.tolist())
    assert len(shared) > 100
    mismatches = 0
    built_by_ts = {int(t): i for i, t in enumerate(built.ts)}
    native_by_ts = {int(t): i for i, t in enumerate(native.ts)}
    for ts in shared:
        b, n = built_by_ts[ts], native_by_ts[ts]
        if (abs(built.open[b] - native.open[n]) > 1e-9 or abs(built.high[b] - native.high[n]) > 1e-9
                or abs(built.low[b] - native.low[n]) > 1e-9 or abs(built.close[b] - native.close[n]) > 1e-9):
            mismatches += 1
    assert mismatches / len(shared) < 0.005, f"{symbol}: {mismatches} of {len(shared)} M15 bars differ"
```

- [ ] **Step 2: Run it**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_runner_equivalence.py -q`
Expected: 10 + up to 3 passed (a few minutes; the M1 configs replay ~2,900 bars per poll).

If a config disagrees: the failure message names the poll. Reproduce that single poll in a scratch script, print both setups, and fix `signals.py` — never the assertion. If the mismatch is the sweep's ATR, check the lookback window length first.

- [ ] **Step 3: Commit**

```bash
git add tests/live_replay/test_runner_equivalence.py
git commit -m "Prove the replay's signals equal the live runners', poll by poll"
```

---

### Task 8: The engine — poll clock, fills, exits, swap

**Files:**
- Create: `backtest/live_replay/engine.py`
- Create: `tests/live_replay/test_engine.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `Flags(poll_clock, spread, gap_ticks, gap_proxy, swap, commission)`; `TradeRecord`; `run(config, m1, spec, fx, *, ticks=None, flags=Flags(), start_balance=50_000.0, spread_scale=1.0) -> list[TradeRecord]`; `POLL_SECONDS`, `TRADING_BREAK_SECONDS`.

- [ ] **Step 1: Write the failing test**

`tests/live_replay/test_engine.py`:

```python
"""One bot, replayed: polls every two minutes, pays the spread, and lets the broker hold the stop."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from backtest.live_replay.configs import BotConfig
from backtest.live_replay.engine import Flags, run
from backtest.live_replay.market import NY, FxSeries, frame_from_bars
from backtest.live_replay.specs import SymbolSpec
from core.models import Bar

USD = FxSeries("USD", None, None)
SYN = SymbolSpec(symbol="SYN", point=0.01, tick_size=0.01, contract_size=1.0, volume_min=0.01,
                 volume_step=0.01, volume_max=1e6, profit_currency="USD", swap_mode=5,
                 swap_long=0.0, swap_short=0.0, swap_rollover3days=5, margin_rate=0.0001,
                 commission_per_lot_usd=0.0)
CONFIG = BotConfig(task="T", family="breakout", symbol="SYN", paper=False, risk_pct=0.005,
                   scan_minutes=1, or_minutes=15, tp_r=4.0, entry_window_end=None)


def _bar(ts: datetime, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(timestamp=ts.astimezone(NY), open=o, high=h, low=low, close=c, volume=1.0, spread=0.1)


def _session(day: datetime, tail: list[tuple[float, float, float, float]]) -> list[Bar]:
    """09:30-09:44 range 100-101, a breakout close of 101.8 on the 09:52 bar, then `tail`."""
    bars = [_bar(day + timedelta(minutes=i), 100.5, 101.0, 100.0, 100.5) for i in range(15)]
    bars += [_bar(day + timedelta(minutes=i), 100.5, 100.9, 100.2, 100.6) for i in range(15, 22)]
    bars.append(_bar(day + timedelta(minutes=22), 100.9, 102.0, 100.9, 101.8))
    bars += [_bar(day + timedelta(minutes=23 + i), *ohlc) for i, ohlc in enumerate(tail)]
    return bars


FLAT = [(101.5, 101.6, 101.2, 101.4)] * 40
DAY = datetime(2026, 9, 10, 9, 30, tzinfo=NY)  # a Thursday


def _run(bars, config=CONFIG, spec=SYN, **kwargs):
    return run(config, frame_from_bars("SYN", 1, bars), spec, USD, **kwargs)


def test_entry_waits_for_the_next_even_minute_poll_and_pays_the_ask() -> None:
    trade = _run(_session(DAY, FLAT))[0]
    # the 09:52 bar closes at 09:53; the 09:54 poll is the first that sees it
    assert trade.entry_time.astimezone(NY).strftime("%H:%M") == "09:54"
    assert trade.entry == pytest.approx(101.6)  # 101.5 open + 0.1 spread
    assert (trade.stop, trade.target) == (100.0, pytest.approx(101.8 + 4 * 1.8))


def test_a_stop_is_filled_at_its_level_and_the_setup_is_never_reopened() -> None:
    tail = list(FLAT)
    tail[2] = (101.5, 101.6, 99.5, 99.8)  # the 09:55 bar trades through the stop
    trades = _run(_session(DAY, tail))
    assert len(trades) == 1
    assert (trades[0].exit, trades[0].exit_reason) == (100.0, "SL")
    assert trades[0].r == pytest.approx((100.0 - 101.6) / (101.6 - 100.0), abs=0.01)


def test_a_stop_after_a_trading_break_fills_at_the_break_bars_close() -> None:
    bars = _session(DAY, FLAT)
    after_break = DAY.replace(hour=18)  # the session gap the daily break leaves
    bars.append(_bar(after_break, 101.5, 101.5, 99.0, 99.2))
    trade = _run(bars)[0]
    assert (trade.exit, trade.exit_reason) == (99.2, "SL_GAP_PROXY")


def test_real_ticks_override_the_gap_proxy() -> None:
    class _Ticks:
        def first_crossing(self, symbol, start, direction, level, seconds=300):
            return 99.6

    bars = _session(DAY, FLAT)
    bars.append(_bar(DAY.replace(hour=18), 101.5, 101.5, 99.0, 99.2))
    trade = _run(bars, ticks=_Ticks())[0]
    assert (trade.exit, trade.exit_reason) == (99.6, "SL_GAP_TICK")


def test_the_old_backtest_assumption_fills_a_gap_stop_at_its_level() -> None:
    bars = _session(DAY, FLAT)
    bars.append(_bar(DAY.replace(hour=18), 101.5, 101.5, 99.0, 99.2))
    trade = _run(bars, flags=Flags(gap_ticks=False, gap_proxy=False))[0]
    assert (trade.exit, trade.exit_reason) == (100.0, "SL_GAP_LEVEL")


def test_without_the_poll_clock_it_enters_on_the_next_bar_like_a_batch_backtest() -> None:
    trade = _run(_session(DAY, FLAT), flags=Flags(poll_clock=False))[0]
    assert trade.entry_time.astimezone(NY).strftime("%H:%M") == "09:53"


def test_with_the_spread_switched_off_it_fills_at_the_bid() -> None:
    trade = _run(_session(DAY, FLAT), flags=Flags(spread=False))[0]
    assert trade.entry == pytest.approx(101.5)


def test_a_position_held_over_the_weekend_is_charged_three_swap_days() -> None:
    friday = datetime(2026, 9, 11, 9, 30, tzinfo=NY)
    bars = _session(friday, FLAT)
    monday = datetime(2026, 9, 14, 9, 30, tzinfo=NY)
    bars += [_bar(monday + timedelta(minutes=i), 101.5, 101.6, 101.2, 101.4) for i in range(3)]
    bars.append(_bar(monday + timedelta(minutes=3), 101.5, 110.0, 101.4, 109.5))  # hits the target
    spec = replace(SYN, swap_long=-7.33)
    trade = _run(bars, spec=spec)[0]
    expected = trade.volume * 1.0 * 101.4 * -7.33 / 100 / 360 * 3
    assert trade.exit_reason == "TP"
    assert trade.swap_usd == pytest.approx(expected, rel=0.01)


def test_a_trade_still_open_at_the_end_is_reported_but_marked_open() -> None:
    trades = _run(_session(DAY, FLAT))
    assert trades[-1].exit_reason == "OPEN"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_engine.py -q`
Expected: `ModuleNotFoundError: No module named 'backtest.live_replay.engine'`.

- [ ] **Step 3: Write the implementation**

`backtest/live_replay/engine.py`:

```python
"""One bot, replayed the way the VPS runs it.

The loop walks M1 bars. While a position is open the broker holds its stop and target, so they
are checked on every bar and swap is charged at each server midnight. While flat, the bot polls
every two minutes (the Scheduled Task interval), sees only bars that have closed, and fills a
market order at the open of the bar its poll lands in.

Each realism feature is a flag, so the report can measure what it costs by switching it off.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from core.models import SignalDirection
from strategy.risk_reward import resolve_stop_and_target

from backtest.live_replay.configs import BotConfig
from backtest.live_replay.market import BROKER_TZ, BarFrame, FxSeries, aggregate
from backtest.live_replay.pricing import entry_price, exit_on_bar, rollover_days, size_position, swap_usd
from backtest.live_replay.signals import make_signals
from backtest.live_replay.specs import SymbolSpec
from backtest.live_replay.ticks import TickCache

POLL_SECONDS = 120            # the VPS Scheduled Task interval
TRADING_BREAK_SECONDS = 1800  # execution/paper_broker.py's _TRADING_BREAK
GAP_TICK_WINDOW_SECONDS = 300


@dataclass(frozen=True)
class Flags:
    """Realism features; each can be switched off to measure what it costs."""

    poll_clock: bool = True   # False: act on the bar after the signal, as the batch backtests do
    spread: bool = True       # False: fills and short exits at the bid
    gap_ticks: bool = True    # False: never price a post-break stop from real ticks
    gap_proxy: bool = True    # False: a post-break stop fills at its level (the batch assumption)
    swap: bool = True
    commission: bool = True


@dataclass(frozen=True)
class TradeRecord:
    task: str
    symbol: str
    setup_id: str
    direction: str
    signal_time: datetime
    entry_time: datetime
    exit_time: datetime
    entry: float
    stop: float
    target: float
    exit: float
    exit_reason: str  # TP | SL | SL_GAP_TICK | SL_GAP_PROXY | SL_GAP_LEVEL | OPEN
    volume: float
    pnl_usd: float
    swap_usd: float
    commission_usd: float
    risk_usd: float
    r: float
    balance_after: float
    closed_on_entry_bar: bool
    both_levels_touched: bool


@dataclass
class _Position:
    setup: object
    direction: SignalDirection
    entry_index: int
    fill: float
    stop: float
    target: float
    volume: float
    risk_usd: float
    commission_usd: float
    swap_usd: float = 0.0


def _server_days(ts: np.ndarray) -> np.ndarray:
    """yyyymmdd of each bar in broker server time, for spotting midnight rollovers."""
    local = pd.DatetimeIndex(pd.to_datetime(ts, unit="s", utc=True)).tz_convert(BROKER_TZ)
    return (local.year * 10000 + local.month * 100 + local.day).to_numpy(dtype=np.int64)


def _as_date(yyyymmdd: int):
    return datetime.strptime(str(int(yyyymmdd)), "%Y%m%d").date()


def run(config: BotConfig, m1: BarFrame, spec: SymbolSpec, fx: FxSeries, *,
        ticks: TickCache | None = None, flags: Flags = Flags(), start_balance: float = 50_000.0,
        spread_scale: float = 1.0) -> list[TradeRecord]:
    scan = aggregate(m1, config.scan_minutes)
    signals = make_signals(config, scan)
    server_day = _server_days(m1.ts)
    traded: set[str] = set()
    trades: list[TradeRecord] = []
    balance = start_balance
    position: _Position | None = None

    def spread_at(j: int) -> float:
        return float(m1.spread[j]) * spread_scale if flags.spread else 0.0

    def close(j: int, price: float, reason: str, *, touched_both: bool = False) -> None:
        nonlocal balance, position
        held = position
        usd = fx.usd_per_unit(int(m1.ts[j]))
        sign = 1.0 if held.direction == SignalDirection.BUY else -1.0
        pnl = ((price - held.fill) * sign * spec.usd_per_price_unit(held.volume, usd)
               + held.swap_usd + held.commission_usd)
        balance += pnl
        trades.append(TradeRecord(
            task=config.task, symbol=config.symbol, setup_id=held.setup.setup_id,
            direction=held.direction.name, signal_time=held.setup.timestamp,
            entry_time=datetime.fromtimestamp(int(m1.ts[held.entry_index]), UTC),
            exit_time=datetime.fromtimestamp(int(m1.ts[j]), UTC), entry=held.fill, stop=held.stop,
            target=held.target, exit=price, exit_reason=reason, volume=held.volume, pnl_usd=pnl,
            swap_usd=held.swap_usd, commission_usd=held.commission_usd, risk_usd=held.risk_usd,
            r=pnl / held.risk_usd if held.risk_usd > 0 else 0.0, balance_after=balance,
            closed_on_entry_bar=j == held.entry_index, both_levels_touched=touched_both))
        position = None

    def check_exit(j: int) -> bool:
        after_break = j > 0 and int(m1.ts[j]) - int(m1.ts[j - 1]) > TRADING_BREAK_SECONDS
        bar = m1.bar(j)
        hit = exit_on_bar(position.direction, position.stop, position.target, bar, spread_at(j),
                          entry_bar=j == position.entry_index, after_break=after_break)
        if hit is None:
            return False
        price, reason = hit
        if position.direction == SignalDirection.BUY:
            touched_both = bar.low <= position.stop and bar.high >= position.target
        else:
            ask_high, ask_low = bar.high + spread_at(j), bar.low + spread_at(j)
            touched_both = ask_high >= position.stop and ask_low <= position.target
        if reason == "SL" and after_break:
            reason = "SL_GAP_PROXY"
            tick_price = (ticks.first_crossing(config.symbol, int(m1.ts[j]), position.direction,
                                               position.stop, GAP_TICK_WINDOW_SECONDS)
                          if flags.gap_ticks and ticks is not None else None)
            if tick_price is not None:
                price, reason = tick_price, "SL_GAP_TICK"
            elif not flags.gap_proxy:
                price, reason = position.stop, "SL_GAP_LEVEL"
        close(j, price, reason, touched_both=touched_both)
        return True

    def poll_time(j: int) -> int | None:
        """The poll this bar serves: an even minute at its open, or the even minute just before
        it when that minute had no bar. A poll during a longer gap hits a closed market, which
        the runner simply retries at its next poll."""
        t = int(m1.ts[j])
        if (t // 60) % 2 == 0:
            return t
        previous = int(m1.ts[j - 1]) if j > 0 else None
        return t - 60 if previous is None or t - 60 > previous else None

    for j in range(len(m1)):
        if position is not None:
            if flags.swap and j > 0 and server_day[j] > server_day[j - 1]:
                days = rollover_days(_as_date(server_day[j - 1]), _as_date(server_day[j]),
                                     spec.swap_rollover3days)
                position.swap_usd += swap_usd(spec, position.direction, position.volume,
                                              float(m1.close[j - 1]), days,
                                              fx.usd_per_unit(int(m1.ts[j])))
            check_exit(j)  # a bar that closes a trade never opens the next one
            continue

        poll = int(m1.ts[j]) if not flags.poll_clock else poll_time(j)
        if poll is None:
            continue
        setup = signals.at(scan.count_closed_by(poll))
        if setup is None or setup.setup_id in traded:
            continue
        usd = fx.usd_per_unit(int(m1.ts[j]))
        volume = size_position(spec, setup, balance, usd, config.risk_pct)
        if volume <= 0:
            continue  # the live sizer would reject it locally and retry at the next poll
        stop, target = resolve_stop_and_target(setup)
        fill = entry_price(setup.direction, m1.bar(j), spread_at(j))
        position = _Position(
            setup=setup, direction=setup.direction, entry_index=j, fill=fill, stop=stop,
            target=target, volume=volume,
            risk_usd=abs(fill - stop) * spec.usd_per_price_unit(volume, usd),
            commission_usd=-spec.commission_per_lot_usd * volume if flags.commission else 0.0)
        traded.add(setup.setup_id)
        check_exit(j)

    if position is not None:
        close(len(m1) - 1, float(m1.close[-1]), "OPEN")
    return trades
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_engine.py -q`
Expected: 9 passed.

- [ ] **Step 5: Time one real configuration**

```bash
.venv/Scripts/python.exe -c "import time; from backtest.live_replay.configs import scope; from backtest.live_replay.engine import run; from backtest.live_replay.market import DEFAULT_DATA_DIR, load_fx, load_m1; from backtest.live_replay.specs import load_specs; c = [x for x in scope() if x.task == 'OrbBreakout_NDX100_Demo'][0]; s = load_specs()['NDX100']; t0 = time.time(); m1 = load_m1('NDX100', DEFAULT_DATA_DIR); t1 = time.time(); tr = run(c, m1, s, load_fx('USD')); print(f'bars {len(m1)} load {t1-t0:.1f}s replay {time.time()-t1:.1f}s trades {len(tr)}'); print(tr[-1])"
```

Expected: a few hundred trades over 6.7 years and a replay well under 5 minutes. If it is slower, profile before continuing — every later task runs this ten times.

- [ ] **Step 6: Commit**

```bash
git add backtest/live_replay/engine.py tests/live_replay/test_engine.py
git commit -m "Replay a bot bar by bar: polls, ask fills, broker stops, swap"
```

---

### Task 9: Reproduce the real trades (gate G3)

**Files:**
- Create: `tests/live_replay/test_real_trades.py`

**Interfaces:**
- Consumes: `run`, `load_m1`, `load_fx`, `load_specs`, `TickCache`, the fixtures in `tests/fixtures/live_replay/`.
- Produces: nothing — the gate that decides whether any number may be reported.

- [ ] **Step 1: Write the test**

`tests/live_replay/test_real_trades.py`:

```python
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
# One spread plus a little slack, in price units, from the 2026 spread column of each CSV.
ENTRY_TOLERANCE = {"NDX100": 3.5, "SPX500": 1.8, "DJI30": 2.5, "GER40": 4.5, "JP225": 12.0,
                   "XAUUSD": 0.3}
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


@pytest.fixture(scope="module")
def replayed() -> dict[str, list]:
    specs = load_specs()
    ticks = TickCache(DEFAULT_DATA_DIR / "ticks")
    out: dict[str, list] = {}
    for config in DEMO_CONFIGS + PAPER_CONFIGS:
        key = f"{config.symbol}_{config.or_minutes}_{config.tp_r}_{config.family}"
        if key in out:
            continue
        m1 = load_m1(config.symbol, DEFAULT_DATA_DIR, start=REPLAY_FROM, end=REPLAY_TO)
        spec = specs[config.symbol]
        out[key] = run(config, m1, spec, load_fx(spec.profit_currency), ticks=ticks)
    return out


def _replayed_for(replayed, config, *, days=None):
    key = f"{config.symbol}_{config.or_minutes}_{config.tp_r}_{config.family}"
    trades = [t for t in replayed[key] if _in_window(t.entry_time.astimezone(BROKER_TZ))]
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
        assert trade.entry == pytest.approx(float(row["open_price"]), abs=ENTRY_TOLERANCE[symbol]), where
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
```

- [ ] **Step 2: Run it**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_real_trades.py -q`
Expected: 7 passed. The first run fetches ticks from MT5 (terminal must be running) and caches them.

- [ ] **Step 3: If anything fails, debug the model — not the tolerance**

Use `superpowers:systematic-debugging`. The likely causes, in order: (a) the data refresh in Task 3 was skipped, so the week is missing; (b) the poll parity in `engine.poll_time` (compare `trade.entry_time` with the real deal second); (c) the gold config dates; (d) the FX file missing for JP225's swap. Only widen a tolerance if you can state, in the commit message, the market reason the two prices differ.

- [ ] **Step 4: Commit**

```bash
git add tests/live_replay/test_real_trades.py
git commit -m "Gate the replay on reproducing the real 2026-09-10..14 trades"
```

---

### Task 10: Metrics

**Files:**
- Create: `backtest/live_replay/metrics.py`
- Create: `tests/live_replay/test_metrics.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Stats(n, win_pct, pf, net_r, avg_r, max_dd_r, worst_streak)`; `stats(rs)`; `since(dated, start)`; `half_year_blocks(dated)`; `FilterResult` with `.passed`; `three_filters(dated, end)`; `equity_curve(pnls_usd, start_balance) -> (final, max_dd_pct)`; `SHORT_HISTORY_DAYS`.

- [ ] **Step 1: Write the failing test**

`tests/live_replay/test_metrics.py`:

```python
"""PF, R, drawdown, half-year blocks and the three deployment filters."""

from datetime import date

import pytest

from backtest.live_replay.metrics import equity_curve, half_year_blocks, stats, three_filters


def test_profit_factor_and_drawdown_of_a_small_series() -> None:
    result = stats([2.0, -1.0, -1.0, 3.0])
    assert result.n == 4
    assert result.win_pct == 50.0
    assert result.pf == pytest.approx(2.5)
    assert result.net_r == pytest.approx(3.0)
    assert result.max_dd_r == pytest.approx(2.0)
    assert result.worst_streak == 2


def test_a_series_without_a_loss_has_no_finite_profit_factor() -> None:
    assert stats([1.0, 2.0]).pf == float("inf")


def test_an_empty_series_is_all_zeroes() -> None:
    assert stats([]).n == 0 and stats([]).pf == 0.0


def test_trades_are_grouped_into_calendar_half_years() -> None:
    blocks = half_year_blocks([(date(2025, 3, 1), 1.0), (date(2025, 8, 1), -1.0),
                               (date(2026, 1, 5), 2.0)])
    assert list(blocks) == ["2025H1", "2025H2", "2026H1"]


def test_the_three_filters_read_each_rule_separately() -> None:
    dated = [(date(2024, 3, 1), 3.0), (date(2024, 9, 1), -1.0), (date(2025, 3, 1), 2.0),
             (date(2025, 9, 1), -1.0), (date(2026, 3, 1), 2.0), (date(2026, 9, 1), 1.0)]
    result = three_filters(dated, end=date(2026, 9, 15))
    assert result.full_ok is True
    assert result.last_year_ok is True
    assert result.blocks_green_pct == pytest.approx(100.0 * 4 / 6)
    assert result.blocks_ok is True
    assert result.passed is True
    assert result.short_history is False


def test_a_symbol_with_under_thirty_months_of_trades_is_flagged_short() -> None:
    dated = [(date(2025, 6, 1), 1.0), (date(2026, 6, 1), 1.0)]
    assert three_filters(dated, end=date(2026, 9, 15)).short_history is True


def test_the_dollar_curve_reports_the_worst_peak_to_trough() -> None:
    final, dd = equity_curve([1000.0, -2000.0, 500.0], 50_000.0)
    assert final == pytest.approx(49_500.0)
    assert dd == pytest.approx(100 * 2000 / 51_000, abs=0.01)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_metrics.py -q`
Expected: `ModuleNotFoundError: No module named 'backtest.live_replay.metrics'`.

- [ ] **Step 3: Write the implementation**

`backtest/live_replay/metrics.py`:

```python
"""The numbers the report prints.

The deployment rule this project uses is: full-history PF > 1, last-year PF > 1, and a
consistent record across periods. The third leg was originally a walk-forward fold record,
which measures a SELECTION process; a fixed configuration has nothing to select, so the
measurable equivalent is the share of calendar half-years that end positive. The report says
so rather than presenting the two as the same test.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

SHORT_HISTORY_DAYS = 913  # 30 months: a 24+6 walk-forward fold does not fit in less


@dataclass(frozen=True)
class Stats:
    n: int
    win_pct: float
    pf: float
    net_r: float
    avg_r: float
    max_dd_r: float
    worst_streak: int


def stats(rs: Sequence[float]) -> Stats:
    if not rs:
        return Stats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)
    wins = [r for r in rs if r > 0]
    losses = -sum(r for r in rs if r <= 0)
    peak = cum = drawdown = 0.0
    streak = worst = 0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        drawdown = max(drawdown, peak - cum)
        streak = streak + 1 if r <= 0 else 0
        worst = max(worst, streak)
    return Stats(n=len(rs), win_pct=100.0 * len(wins) / len(rs),
                 pf=(sum(wins) / losses) if losses > 0 else float("inf"),
                 net_r=sum(rs), avg_r=sum(rs) / len(rs), max_dd_r=drawdown, worst_streak=worst)


def since(dated: Sequence[tuple[date, float]], start: date | None) -> list[float]:
    return [r for day, r in dated if start is None or day >= start]


def half_year_blocks(dated: Sequence[tuple[date, float]]) -> dict[str, list[float]]:
    blocks: dict[str, list[float]] = defaultdict(list)
    for day, r in dated:
        blocks[f"{day.year}H{1 if day.month <= 6 else 2}"].append(r)
    return dict(sorted(blocks.items()))


@dataclass(frozen=True)
class FilterResult:
    full_pf: float
    last_year_pf: float
    blocks_green_pct: float
    blocks: int
    full_ok: bool
    last_year_ok: bool
    blocks_ok: bool
    short_history: bool

    @property
    def passed(self) -> bool:
        return self.full_ok and self.last_year_ok and self.blocks_ok


def three_filters(dated: Sequence[tuple[date, float]], end: date) -> FilterResult:
    full = stats([r for _, r in dated]).pf
    last_year = stats(since(dated, end - timedelta(days=365))).pf
    blocks = half_year_blocks(dated)
    green = [sum(rs) > 0 for rs in blocks.values()]
    green_pct = 100.0 * sum(green) / len(green) if green else 0.0
    first = min((day for day, _ in dated), default=end)
    return FilterResult(full_pf=full, last_year_pf=last_year, blocks_green_pct=green_pct,
                        blocks=len(blocks), full_ok=full > 1.0, last_year_ok=last_year > 1.0,
                        blocks_ok=green_pct >= 60.0,
                        short_history=(end - first).days < SHORT_HISTORY_DAYS)


def equity_curve(pnls_usd: Sequence[float], start_balance: float) -> tuple[float, float]:
    """Final balance and worst peak-to-trough drawdown, in percent."""
    balance = peak = start_balance
    worst = 0.0
    for pnl in pnls_usd:
        balance += pnl
        peak = max(peak, balance)
        worst = max(worst, (peak - balance) / peak)
    return balance, 100.0 * worst
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_metrics.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest/live_replay/metrics.py tests/live_replay/test_metrics.py
git commit -m "Score a replayed configuration against the three deployment filters"
```

---

### Task 11: The runner script — old column, ablation, spread calibration, report

**Files:**
- Create: `scripts/live_replay_backtest.py`
- Create: `tests/live_replay/test_report.py`
- Modify: `.gitignore` (ignore the per-trade CSVs)

**Interfaces:**
- Consumes: everything above, plus `scripts.nasdaq_orb_m1_breakout_backtest.run_backtest`, `scripts.xauusd_orb_liquidity_sweep_backtest.run_backtest`, `scripts.two_strategy_symbol_sweep.recent_spread`.
- Produces: `ConfigResult`; `old_rs(config, data_dir)`; `spread_ratio(trades, m1, ticks)`; `ablation(...)`; `write_trades_csv(path, trades)`; `render_report(results, end, generated, validation_note)`; `RECORDED_OLD_PF`, `RECORDED_OLD_END`; `main()`.

- [ ] **Step 1: Write the failing test**

`tests/live_replay/test_report.py`:

```python
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
                         validation_note="G1-G3: 40 passed")
    assert "OrbBreakout_NDX100_Demo" in text
    assert "kohne" in text.lower() or "köhnə" in text
    assert "1.329" in text and "G1-G3: 40 passed" in text
    assert "swap" in text and "spread" in text          # the decomposition
    assert "mehdudiyyet" in text.lower() or "məhdudiyyət" in text.lower()


def test_every_trade_is_written_to_the_csv(tmp_path) -> None:
    path = tmp_path / "trades.csv"
    write_trades_csv(path, [_trade(10, 4.0), _trade(11, -1.0, "SL")])
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert "setup_id" in lines[0] and "s10" in lines[1]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_report.py -q`
Expected: `ModuleNotFoundError: No module named 'scripts.live_replay_backtest'`.

- [ ] **Step 3: Write the implementation**

`scripts/live_replay_backtest.py`:

```python
"""Replay every deployed ORB bot the way the VPS runs it, beside the old batch backtests.

Usage:
    python -m scripts.live_replay_backtest
    python -m scripts.live_replay_backtest --configs OrbSweep_GER40_Demo --no-ablation
    python -m scripts.live_replay_backtest --data-dir data/history/fundingpips --out artifacts/live_replay

Writes artifacts/live_replay/<task>_trades.csv and LIVE_REPLAY_BACKTEST_REPORT.md.
Read the spec before changing any of this: docs/superpowers/specs/2026-09-15-live-replay-backtest-design.md
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

import numpy as np

import scripts.nasdaq_orb_m1_breakout_backtest as orb_mod
import scripts.xauusd_orb_liquidity_sweep_backtest as sweep_mod
from backtest.live_replay.configs import BotConfig, scope
from backtest.live_replay.engine import Flags, TradeRecord, run
from backtest.live_replay.market import DEFAULT_DATA_DIR, BarFrame, load_fx, load_m1
from backtest.live_replay.metrics import equity_curve, stats, three_filters
from backtest.live_replay.specs import load_specs
from backtest.live_replay.ticks import TickCache
from scripts.two_strategy_symbol_sweep import recent_spread

REPORT_PATH = Path("LIVE_REPLAY_BACKTEST_REPORT.md")
ABLATIONS = ("poll_clock", "spread", "gap_ticks", "gap_proxy", "swap", "commission")
# PFs this repo already recorded for these exact configurations, on data ending ~2026-09-08
# (deploy/demo_roster.txt comments, WALK_FORWARD_2026_09_09_REPORT.md). The old column is only
# trustworthy if it still reproduces them.
RECORDED_OLD_END = date(2026, 9, 8)
RECORDED_OLD_PF = {
    "OrbBreakout_XAUUSD_Demo": 1.235, "OrbBreakout_NDX100_Demo": 1.329,
    "OrbBreakout_SPX500_Demo": 1.326, "OrbBreakout_DJI30_Demo": 1.205,
    "OrbBreakout_JP225_Demo": 1.177, "OrbSweep_GER40_Demo": 1.547,
    "OrbBreakout_XAUUSD_Paper": 1.389, "OrbSweep_JP225_Paper": 1.253,
}


@dataclass
class ConfigResult:
    config: BotConfig
    trades: list[TradeRecord]          # closed trades only
    old: list[tuple[date, float]]
    ablation: dict[str, float]         # feature switched off -> net R without it
    spread_ratio: float | None
    recorded_pf: float | None
    reproduced_pf: float | None


def old_rs(config: BotConfig, data_dir: Path) -> list[tuple[date, float]]:
    """The batch backtest's own trades, called exactly as scripts/live_vs_backtest_report.py does."""
    csv_path = Path(data_dir) / f"{config.symbol}_M1.csv"
    spread = recent_spread(csv_path)
    if config.family == "breakout":
        trades = orb_mod.run_backtest(str(csv_path), "full", spread, config.tp_r, "long",
                                      or_minutes=config.or_minutes, scan_minutes=config.scan_minutes)
    else:
        hour, minute = ((int(p) for p in config.entry_window_end.split(":"))
                        if config.entry_window_end else (11, 0))
        trades, _ = sweep_mod.run_backtest(str(csv_path), tp_r=2.0, spread_points=spread,
                                           enable_breakout=False, bar_minutes=15,
                                           entry_window_end=time(hour, minute),
                                           entry_fill_mode="next_open")
    return [(date.fromisoformat(str(t.day)[:10]), t.r_multiple) for t in trades]


def spread_ratio(trades: list[TradeRecord], m1: BarFrame, ticks: TickCache) -> float | None:
    """Median (tick spread / bar spread) at the entries the broker still has ticks for."""
    ratios: list[float] = []
    for trade in trades:
        start = int(trade.entry_time.timestamp())
        if not ticks.has_history(trade.symbol, start):
            continue
        rows = ticks.window(trade.symbol, start, 60)
        index = int(np.searchsorted(m1.ts, start))
        if not rows or index >= len(m1) or m1.spread[index] <= 0:
            continue
        ratios.append(statistics.median(ask - bid for _, bid, ask in rows) / float(m1.spread[index]))
    return statistics.median(ratios) if ratios else None


def write_trades_csv(path: Path, trades: list[TradeRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(trades[0]).keys()) if trades else ["setup_id"])
        writer.writeheader()
        for trade in trades:
            writer.writerow(asdict(trade))


def _dated(trades: list[TradeRecord]) -> list[tuple[date, float]]:
    return [(t.entry_time.date(), t.r) for t in trades if t.exit_reason != "OPEN"]


def _fmt(value: float) -> str:
    return "inf" if value == float("inf") else f"{value:.3f}"


def render_report(results: list[ConfigResult], end: date, generated: datetime,
                  validation_note: str) -> str:
    lines = [
        "# Canlı Əkiz Backtest — nəticə",
        "",
        f"Hazırlandı: {generated:%Y-%m-%d %H:%M} UTC · data sonu: {end:%Y-%m-%d} · "
        "spec: `docs/superpowers/specs/2026-09-15-live-replay-backtest-design.md`",
        "",
        "## Yoxlamalar",
        "",
        validation_note,
        "",
        "## Əsas cədvəl (köhnə backtest vs canlı əkiz)",
        "",
        "| Bot | köhnə n | köhnə PF | köhnə netR | əkiz n | əkiz PF | əkiz netR | əkiz maxDD R | "
        "son 1 il PF (köhnə → əkiz) | 6 aylıq blok yaşıl % | filtrlər |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for result in results:
        mine = _dated(result.trades)
        old_stats, new_stats = stats([r for _, r in result.old]), stats([r for _, r in mine])
        old_filters, new_filters = three_filters(result.old, end), three_filters(mine, end)
        marks = ("✅" if new_filters.full_ok else "❌") + ("✅" if new_filters.last_year_ok else "❌") \
            + ("✅" if new_filters.blocks_ok else "❌") + (" ⚠️az tarixçə" if new_filters.short_history else "")
        lines.append(
            f"| {result.config.task} | {old_stats.n} | {_fmt(old_stats.pf)} | {old_stats.net_r:+.1f} | "
            f"{new_stats.n} | {_fmt(new_stats.pf)} | {new_stats.net_r:+.1f} | {new_stats.max_dd_r:.1f} | "
            f"{_fmt(old_filters.last_year_pf)} → {_fmt(new_filters.last_year_pf)} | "
            f"{new_filters.blocks_green_pct:.0f}% ({new_filters.blocks}) | {marks} |")

    lines += ["", "Filtrlər sırası: tam tarixçə PF > 1, son 1 il PF > 1, 6 aylıq blokların ≥60%-i müsbət.",
              "", "## Fərqin parçalanması (netR, xüsusiyyət söndürüləndə)", "",
              "| Bot | tam əkiz | " + " | ".join(ABLATIONS) + " |",
              "|---|---|" + "---|" * len(ABLATIONS)]
    for result in results:
        full = stats([r for _, r in _dated(result.trades)]).net_r
        cells = " | ".join(f"{result.ablation[name]:+.1f}" if name in result.ablation else "—"
                           for name in ABLATIONS)
        lines.append(f"| {result.config.task} | {full:+.1f} | {cells} |")

    lines += ["", "## $50,000 hesabda (hər bot ayrıca)", "",
              "| Bot | son balans | max drawdown % | trade | swap $ | komissiya $ |",
              "|---|---|---|---|---|---|"]
    for result in results:
        closed = [t for t in result.trades if t.exit_reason != "OPEN"]
        final, drawdown = equity_curve([t.pnl_usd for t in closed], 50_000.0)
        lines.append(f"| {result.config.task} | ${final:,.0f} | {drawdown:.1f}% | {len(closed)} | "
                     f"${sum(t.swap_usd for t in closed):,.0f} | "
                     f"${sum(t.commission_usd for t in closed):,.0f} |")

    lines += ["", "## Çıxış növləri və köhnə sütunun yoxlanması", "",
              "| Bot | TP | SL | boşluq (tick) | boşluq (proksi) | açıq | eyni bar SL+TP | "
              "giriş barında bağlanan | spread nisbəti | qeydə alınmış köhnə PF | təkrar |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for result in results:
        reasons = [t.exit_reason for t in result.trades]
        ratio = "—" if result.spread_ratio is None else f"{result.spread_ratio:.2f}x"
        recorded = "—" if result.recorded_pf is None else f"{result.recorded_pf:.3f}"
        reproduced = "—" if result.reproduced_pf is None else f"{result.reproduced_pf:.3f}"
        lines.append(
            f"| {result.config.task} | {reasons.count('TP')} | {reasons.count('SL')} | "
            f"{reasons.count('SL_GAP_TICK')} | {reasons.count('SL_GAP_PROXY') + reasons.count('SL_GAP_LEVEL')} | "
            f"{reasons.count('OPEN')} | {sum(1 for t in result.trades if t.both_levels_touched)} | "
            f"{sum(1 for t in result.trades if t.closed_on_entry_bar)} | {ratio} | {recorded} | {reproduced} |")

    lines += [
        "", "## Məhdudiyyətlər", "",
        "- Swap dərəcələri tarixi deyil: bütün tarixçəyə 2026-09-15 dərəcələri tətbiq olunub.",
        "- Spread hər M1 barın öz spread sütunundandır; tick müqayisəsi yuxarıdakı nisbətdədir.",
        "- Tick tarixçəsi indekslərdə 2025-03, qızılda 2026-05-dən başlayır; ondan əvvəlki boşluq "
        "stopları bar close proksisi ilə qiymətləndirilib (`gap_proxy` sütunu bunun qiymətidir).",
        "- Poll saniyəsi sabit götürülüb; real jitter 4–6 saniyədir.",
        "- Requote, reject, AutoTrading kəsintiləri və FundingPips-in məcburi bağlanışları modelləşdirilmir.",
        "- Botlarda heç nə dəyişmir; 2026-10-12 dondurma planı qüvvədədir.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs", default="", help="comma-separated task names; default all ten")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--out", default="artifacts/live_replay")
    parser.add_argument("--no-ablation", action="store_true")
    parser.add_argument("--no-old", action="store_true")
    parser.add_argument("--validation-note", default="artifacts/live_replay/validation.md")
    args = parser.parse_args()

    data_dir, out_dir = Path(args.data_dir), Path(args.out)
    wanted = {name.strip() for name in args.configs.split(",") if name.strip()}
    configs = [c for c in scope() if not wanted or c.task in wanted]
    specs = load_specs()
    ticks = TickCache(data_dir / "ticks")
    results: list[ConfigResult] = []
    end = date.min  # "last year" is measured back from the newest bar, not from today

    for config in configs:
        print(f"--- {config.task}")
        spec = specs[config.symbol]
        m1 = load_m1(config.symbol, data_dir)  # ~150MB: load once, replay it seven times
        fx = load_fx(spec.profit_currency, data_dir)
        trades = run(config, m1, spec, fx, ticks=ticks)
        end = max(end, datetime.fromtimestamp(int(m1.ts[-1]), UTC).date())
        ablation: dict[str, float] = {}
        if not args.no_ablation:
            for name in ABLATIONS:
                without = run(config, m1, spec, fx, ticks=ticks, flags=Flags(**{name: False}))
                ablation[name] = stats([t.r for t in without if t.exit_reason != "OPEN"]).net_r
        old = [] if args.no_old else old_rs(config, data_dir)
        recorded = RECORDED_OLD_PF.get(config.task)
        reproduced = (stats([r for day, r in old if day <= RECORDED_OLD_END]).pf
                      if old and recorded is not None else None)
        results.append(ConfigResult(config=config, trades=trades, old=old, ablation=ablation,
                                    spread_ratio=spread_ratio(trades, m1, ticks),
                                    recorded_pf=recorded, reproduced_pf=reproduced))
        write_trades_csv(out_dir / f"{config.task}_trades.csv", trades)

    note_path = Path(args.validation_note)
    note = note_path.read_text(encoding="utf-8") if note_path.exists() else "(testlər işlədilməyib)"
    REPORT_PATH.write_text(render_report(results, end, datetime.now(UTC), note), encoding="utf-8")
    print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
```

Append to `.gitignore` (the report is committed; the per-trade CSVs are regenerated):

```
# Regenerated by scripts/live_replay_backtest.py on every run.
artifacts/live_replay/
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/live_replay/test_report.py -q`
Expected: 2 passed.

- [ ] **Step 5: Smoke-run one configuration end to end**

```bash
.venv/Scripts/python.exe -m scripts.live_replay_backtest --configs OrbSweep_GER40_Demo --no-ablation
```

Expected: `--- OrbSweep_GER40_Demo`, then `wrote LIVE_REPLAY_BACKTEST_REPORT.md`, with a GER40 row whose old PF is near the recorded 1.547 and whose twin PF is lower (the spec predicts ~1.3 once the end-of-day close is gone).

- [ ] **Step 6: Commit**

```bash
git add scripts/live_replay_backtest.py tests/live_replay/test_report.py .gitignore
git commit -m "Report the replay beside the old backtest, with the cost of each realism rule"
```

---

### Task 12: Full run, report, hand-off

**Files:**
- Create: `LIVE_REPLAY_BACKTEST_REPORT.md` (generated), `artifacts/live_replay/validation.md` (generated, gitignored)
- Modify: memory file `project_live_replay_backtest.md`

- [ ] **Step 1: Run the whole suite and record the gate result**

```bash
mkdir -p artifacts/live_replay && .venv/Scripts/python.exe -m pytest tests/live_replay -q > artifacts/live_replay/validation_raw.txt 2>&1; tail -5 artifacts/live_replay/validation_raw.txt
```

Expected: everything passes. Then write the note the report embeds:

```bash
printf '%s\n' "G1-G5 yoxlamaları: $(tail -1 artifacts/live_replay/validation_raw.txt)" "Baza: tests/test_nasdaq_midline_sweep_regression.py::test_midline_sweep_ustec_oos_regression bu işdən əvvəl də sınıq idi." > artifacts/live_replay/validation.md
```

If any live_replay test fails, STOP: no numbers are reported until the gates pass.

- [ ] **Step 2: Run the full replay**

```bash
.venv/Scripts/python.exe -m scripts.live_replay_backtest
```

Expected: ten sections, then `wrote LIVE_REPLAY_BACKTEST_REPORT.md`. Budget ~10-30 minutes (each config replays seven times for the ablation).

- [ ] **Step 3: Check the report before believing it**

Read `LIVE_REPLAY_BACKTEST_REPORT.md` and confirm, in this order:
1. The `qeydə alınmış köhnə PF` and `təkrar` columns agree within ±0.01 for the eight configs that have a recorded value. If not, say so in the hand-off — the old column, not the twin, is the suspect.
2. No configuration has zero twin trades (a silent data or config failure).
3. GER40 Sweep's twin PF is well below its old 1.547 — the end-of-day close is gone (`project-ger40-sweep-eod-mismatch`).
4. The `gap_proxy` and `gap_ticks` ablation columns are negative-going for JP225/NDX100 — session-open gaps cost R (`project-session-open-stop-fills`).
5. The spread ratio is near 1.0; a ratio above 1.10 means the bar spread understates the real cost, so rerun with `spread_scale` and report both.

- [ ] **Step 4: Run the full test suite once**

```bash
.venv/Scripts/python.exe -m pytest -q 2>&1 | tail -5
```

Expected: only the pre-existing `test_midline_sweep_ustec_oos_regression` failure.

- [ ] **Step 5: Commit the report**

```bash
git add LIVE_REPLAY_BACKTEST_REPORT.md
git commit -m "Report what the deployed bots would really have done"
```

- [ ] **Step 6: Update the memory file**

Rewrite `C:\Users\Microsol\.claude\projects\C--Users-Microsol-Desktop-github-tr-tradebot\memory\project_live_replay_backtest.md` so it records: the branch and commits, that the gates passed, the headline per-config twin PF vs old PF, which configs now fail the three filters, and what the ablation says each realism rule costs. Update the `MEMORY.md` index line to match.

- [ ] **Step 7: Hand off — do not merge**

Report to the user in Azerbaijani: the gate results, the main table, which bots fail a filter and by how much, and the ablation. State plainly that nothing was merged to main and no bot changed, and ask whether to merge the branch or keep the work aside until the 2026-10-12 review.

---

## Self-Review

**Spec coverage:** §2 scope → Task 1; §3 data → Tasks 2, 3, 5; §4.1-4.2 clock and signals → Tasks 6, 7, 8; §4.3-4.4 entry and sizing → Tasks 4, 8; §4.5 exits → Tasks 4, 8; §4.6 gaps → Tasks 5, 8; §4.7 swap → Tasks 4, 8; §5 architecture → the file layout of Tasks 1-11; §6 gates G1 → Tasks 1-6, 8, 10, 11, G2 → Task 7, G3 → Task 9, G4 → Task 11 (`spread_ratio`), G5 → Task 11 (`RECORDED_OLD_PF`); §7 outputs → Tasks 11, 12; §8 limitations → `render_report`.

**Known gaps, deliberately:** the sweep's own unit coverage is thin (its synthetic setups are hard to hand-build) and rests on Task 7's equivalence test against the real runner; `OrbSweep_XAUUSD_Paper` and `OrbBreakout_GER40_Paper` have no recorded old PF, so G5 skips them; the report's `end` date is the newest bar, so "last year" moves with the data refresh.

## Execution notes (2026-09-15)

Deviations made while executing, each forced by a gate:

- **Entries fill at a tick, not the bar open (found by G3, commit c67f21c).** Filling at the
  M1 bar's open missed two of the ten real 2026-09-10..14 Demo entries by 10-15 points. The real
  orders were stamped :04-:06 into their minute and MT5 truncates deal times to the second, so the
  true mean latency is ~5.8 s. `TickCache.entry_quote` and `engine.POLL_OFFSET_SECONDS = 6` now
  fill at the first tick six seconds into the poll's minute where ticks exist, falling back to the
  bar open elsewhere; `Flags.entry_ticks` makes it ablatable and the report lists it.
- **G3 judges entries by the quote range, not a point tolerance (commit aad5e94).** No fixed
  latency reproduces every fill: the real orders landed anywhere in :04-:07, and JP225/NDX100 moved
  up to 15 points inside that window. The test asserts the replay's entry lies within that window's
  quote range plus ~0.3 spread of slippage; exits keep the one-spread tolerance.
- The spec's §4.3 and §6 G3 were updated to match (same commit as the entry change).
- **2026-09-16: an eleventh configuration.** main gained `OrbBreakoutwf_XAUUSD_Paper` (60m/3R with
  `--weekend-flat`), whose launcher token `breakoutwf` made `parse_bat` raise. The family now comes
  from the runner a launcher calls, `BotConfig.weekend_flat` carries the rule, and the engine closes
  at the Friday 23:40 server poll and takes no entries after it -- the runner's own cutoff, pinned
  minute by minute by `tests/live_replay/test_weekend_flat_parity.py`.
