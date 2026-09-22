"""The bot configurations, read from the launchers the machines execute.

Parameters are parsed from run_live_orb_*.bat and deploy/demo_roster.txt at run time, never
copied, so the replay always describes what is deployed. Defaults mirror the runners' own
argparse defaults (run_live_nasdaq_orb.py: --scan-timeframe M1, --or-minutes 15;
run_live_xauusd_orb.py: --timeframe M15, --entry-window-end unset).

A launcher names the symbol THIS REPO uses (XAUUSD, NDX100), never a broker's own ticker: the
same launcher set runs on the VPS's CFI account and on the workstation's FundingPips one, and
each machine resolves the ticker from its own .env (config/brokers.py). So a BotConfig says
what a bot does, not which broker it does it on -- that is the machine's, and the caller's.
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
    inverse: bool = False         # --inverse: every setup mirrored (strategy/inverse.py)
    reverse_on_stop_r: float | None = None  # --reverse-on-stop R (execution/stop_and_reverse.py)

    @property
    def key(self) -> tuple:
        """What makes two launchers the same strategy, ignoring Demo/Paper.

        Not the broker: one launcher set is deployed on both accounts, so a Demo bot and the
        Paper bot with these same parameters are twins on whichever machine they run.
        """
        return (self.family, self.symbol, self.scan_minutes, self.or_minutes, self.tp_r,
                self.entry_window_end, self.weekend_flat, self.inverse, self.reverse_on_stop_r)


def _flag(text: str, name: str, default: str | None = None) -> str | None:
    match = re.search(rf"--{name}\s+(\S+)", text)
    return match.group(1) if match else default


def task_name(bat: str) -> str:
    """run_live_orb_breakout_xauusd_demo.bat -> OrbBreakout_XAUUSD_Demo,
    run_live_fvg_window_ndx100_demo.bat -> FvgWindow_NDX100_Demo (install_tasks.ps1's own rule)."""
    stem = bat.removeprefix("run_live_").removesuffix(".bat")
    prefix, family, symbol, mode = stem.split("_")
    return f"{prefix.capitalize()}{family.capitalize()}_{symbol.upper()}_{mode.capitalize()}"


def launcher_symbol(path: Path) -> str:
    """The --symbol any launcher passes (this repo's name, not the broker's ticker), whichever runner."""
    symbol = _flag(path.read_text(encoding="utf-8"), "symbol")
    if symbol is None:
        raise ValueError(f"{path.name}: no --symbol")
    return symbol


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
    reverse = _flag(text, "reverse-on-stop")
    common = dict(task=f"Orb{name.capitalize()}_{symbol_tag.upper()}_{mode.capitalize()}",
                  family=family, symbol=symbol,
                  paper="--paper" in text, risk_pct=float(risk),
                  weekend_flat="--weekend-flat" in text, inverse="--inverse" in text,
                  reverse_on_stop_r=float(reverse) if reverse is not None else None)
    if family == "breakout":
        tp_r = _flag(text, "tp-r")
        if tp_r is None:
            raise ValueError(f"{path.name}: --tp-r is required by run_live_nasdaq_orb.py")
        return BotConfig(**common, scan_minutes=_TF_MINUTES[_flag(text, "scan-timeframe", "M1")],
                         or_minutes=int(_flag(text, "or-minutes", "15")), tp_r=float(tp_r),
                         entry_window_end=None)
    return BotConfig(**common, scan_minutes=_TF_MINUTES[_flag(text, "timeframe", "M15")],
                     or_minutes=None, tp_r=None, entry_window_end=_flag(text, "entry-window-end"))


def load_roster(repo: Path = REPO) -> dict[str, str]:
    """Which Demo task may place real orders, and on whose account: task name -> broker name.

    Real-order permission is per broker, not per bot. The one Demo bot in the roster was sized,
    stopped and stop-ruled on CFI's replay -- its lot minimum, its spread, its swap -- so the
    same launcher running on the FundingPips machine is a DIFFERENT deployment and stays paper
    until someone does that work for it. Membership tests still read naturally: a dict answers
    `task in roster` on its keys.
    """
    lines = (repo / "deploy" / "demo_roster.txt").read_text(encoding="utf-8").splitlines()
    rows = [parts for parts in (line.split("#", 1)[0].split() for line in lines) if parts]
    for parts in rows:
        if len(parts) < 2:
            raise ValueError(
                f"demo_roster.txt: {parts[0]} names no broker -- write `{parts[0]} cfi`. "
                f"A Demo bot may only place real orders on the account it was validated on."
            )
    return {parts[0]: parts[1] for parts in rows}


def scope(repo: Path = REPO, broker: str | None = None) -> list[BotConfig]:
    """Every deployed Demo bot, plus each Paper config that is not one of them.

    `broker` narrows the Demo side to the bots that broker's account may really trade; without
    it every rostered bot counts, whichever account it belongs to.
    """
    configs = [parse_bat(p) for p in sorted(repo.glob("run_live_orb_*.bat"))]
    roster = load_roster(repo)
    demo = [c for c in configs if not c.paper and c.task in roster
            and (broker is None or roster[c.task] == broker)]
    demo_keys = {c.key for c in demo}
    paper = [c for c in configs if c.paper and c.key not in demo_keys]
    return demo + paper
