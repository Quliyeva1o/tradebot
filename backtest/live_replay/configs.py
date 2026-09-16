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
    weekend_flat: bool = False    # breakout only: --weekend-flat, flat from Friday 23:40 server time

    @property
    def key(self) -> tuple:
        """What makes two launchers the same strategy, ignoring Demo/Paper."""
        return (self.family, self.symbol, self.scan_minutes, self.or_minutes, self.tp_r,
                self.entry_window_end, self.weekend_flat)


def _flag(text: str, name: str, default: str | None = None) -> str | None:
    match = re.search(rf"--{name}\s+(\S+)", text)
    return match.group(1) if match else default


# The strategy family is whichever runner a launcher calls. The file name's first token only names
# the task, so a variant launcher such as run_live_orb_breakoutwf_xauusd_paper.bat needs no case.
_RUNNER_FAMILY = {"run_live_nasdaq_orb.py": "breakout", "run_live_xauusd_orb.py": "sweep"}


def parse_bat(path: Path) -> BotConfig:
    """Reads one run_live_orb_*.bat into a BotConfig."""
    text = path.read_text(encoding="utf-8")
    name, symbol_tag, mode = path.stem.removeprefix("run_live_orb_").split("_")
    family = next((f for runner, f in _RUNNER_FAMILY.items() if runner in text), None)
    if family is None:
        raise ValueError(f"{path.name}: calls neither run_live_nasdaq_orb.py nor run_live_xauusd_orb.py")
    symbol, risk = _flag(text, "symbol"), _flag(text, "risk-per-trade-pct")
    if symbol is None or risk is None:
        raise ValueError(f"{path.name}: --symbol and --risk-per-trade-pct are required")
    common = dict(task=f"Orb{name.capitalize()}_{symbol_tag.upper()}_{mode.capitalize()}",
                  family=family, symbol=symbol, paper="--paper" in text, risk_pct=float(risk),
                  weekend_flat="--weekend-flat" in text)
    if family == "breakout":
        tp_r = _flag(text, "tp-r")
        if tp_r is None:
            raise ValueError(f"{path.name}: --tp-r is required by run_live_nasdaq_orb.py")
        return BotConfig(**common, scan_minutes=_TF_MINUTES[_flag(text, "scan-timeframe", "M1")],
                         or_minutes=int(_flag(text, "or-minutes", "15")), tp_r=float(tp_r),
                         entry_window_end=None)
    return BotConfig(**common, scan_minutes=_TF_MINUTES[_flag(text, "timeframe", "M15")],
                     or_minutes=None, tp_r=None, entry_window_end=_flag(text, "entry-window-end"))


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
