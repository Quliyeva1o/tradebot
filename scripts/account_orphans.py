"""Real positions and pending orders on this account that no Demo bot of this account manages.

A Demo bot is the only thing that looks after what it opens: it moves nothing itself, but it is
the one that cancels its leftovers and would notice a trade it did not expect. Stand the bot down
-- take it out of deploy/demo_roster.txt, or move the machine to another broker -- while something
of its is still at the broker, and that something is on its own. Nothing flagged it.

That happened on 2026-09-21. The VPS cut its FundingPips Demo bots to one CFI bot and moved to
CFI while a FundingPips SPX500 trade was open and two --reverse-on-stop orders rested beside it.
The NDX100 one outlived its trade's target and was still waiting, a day later, to open a 0.09-lot
short nobody would manage; neither order ever expires. They were found only by looking.

"Managed" here means a Demo bot the roster permits on THIS account trades that symbol. On such a
symbol a reverse order still needs its own trade open, since a bot cancels one only when it polls
and sees the trade gone. Read-only: nothing here cancels or closes anything -- that is a person's
decision, made in the terminal.

Usage (from the checkout whose account to check):
    python -m scripts.account_orphans
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from backtest.live_replay.configs import REPO, launcher_symbol, load_roster, task_name  # noqa: E402
from config.brokers import BrokerProfile  # noqa: E402
from execution.stop_and_reverse import REVERSE_MARKER  # noqa: E402

ORDER_TYPES = {0: "BUY", 1: "SELL", 2: "BUY_LIMIT", 3: "SELL_LIMIT", 4: "BUY_STOP", 5: "SELL_STOP",
               6: "BUY_STOP_LIMIT", 7: "SELL_STOP_LIMIT"}


@dataclass(frozen=True)
class Orphan:
    kind: str       # "movqe" | "pending"
    ticket: int
    symbol: str
    what: str       # side, lots, price
    comment: str
    why: str

    def line(self) -> str:
        return f"{self.kind} #{self.ticket} {self.symbol} {self.what} '{self.comment}' -- {self.why}"


def owned_symbols(profile: BrokerProfile, repo: Path = REPO) -> dict[str, str]:
    """This account's ticker -> the Demo task the roster lets trade it here."""
    roster = load_roster(repo)
    owned = {}
    for bat in sorted([*repo.glob("run_live_orb_*_demo.bat"), *repo.glob("run_live_fvg_*_demo.bat")]):
        task = task_name(bat.name)
        if profile.name in roster.get(task, ()):
            owned[profile.ticker(launcher_symbol(bat))] = task
    return owned


def find_orphans(positions: Iterable, orders: Iterable, owned: dict[str, str]) -> list[Orphan]:
    """The positions and pending orders no rostered Demo bot of this account looks after.

    `positions` and `orders` are MT5's own records (positions_get() / orders_get()).
    """
    positions, orders = list(positions), list(orders)
    open_tickets = [str(p.ticket) for p in positions]
    nobody = "bu hesabin rosterinde bu simvolda Demo bot yoxdur"
    out = []
    for p in positions:
        if p.symbol not in owned:
            out.append(Orphan("movqe", p.ticket, p.symbol,
                              f"{ORDER_TYPES.get(p.type, p.type)} {p.volume} @ {p.price_open}", p.comment, nobody))
    for o in orders:
        what = f"{ORDER_TYPES.get(o.type, o.type)} {o.volume_current} @ {o.price_open}"
        if o.symbol not in owned:
            out.append(Orphan("pending", o.ticket, o.symbol, what, o.comment, nobody))
            continue
        # reverse_comment() keeps only the parent ticket's trailing digits
        parent = o.comment.rsplit(REVERSE_MARKER, 1)[1] if REVERSE_MARKER in o.comment else None
        if parent is not None and not (parent and any(t.endswith(parent) for t in open_tickets)):
            out.append(Orphan("pending", o.ticket, o.symbol, what, o.comment,
                              "reverse order-in aid oldugu movqe artiq aciq deyil"))
    return out


def read(profile: BrokerProfile, repo: Path = REPO) -> list[Orphan]:
    """find_orphans() on the attached terminal's account. Needs mt5.connector.initialize_terminal()."""
    import MetaTrader5 as mt5  # noqa: N813 -- here, so the pure function above needs no terminal

    positions, orders = mt5.positions_get(), mt5.orders_get()
    if positions is None or orders is None:
        # None is an error, not "nothing open": reading it as empty would report a clean account
        raise RuntimeError(f"MT5 did not list positions/orders: {mt5.last_error()}")
    return find_orphans(positions, orders, owned_symbols(profile, repo))


def fetch(profile: BrokerProfile, repo: Path = REPO) -> list[Orphan]:
    """read(), attaching to this checkout's own terminal and detaching after."""
    import MetaTrader5 as mt5  # noqa: N813

    from mt5.connector import initialize_terminal

    if not initialize_terminal():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        return read(profile, repo)
    finally:
        mt5.shutdown()


def report_lines(orphans: list[Orphan], profile: BrokerProfile) -> list[str]:
    if not orphans:
        return [f"   {profile.name} ({profile.server}): yoxdur -- hesabdaki her sey rosterdeki bir botundur"]
    lines = [f"   >>> DIQQET: {profile.name} ({profile.server}) hesabinda {len(orphans)} sey var ki, "
             f"hec bir bot idare etmir.",
             "       Terminalda baxin: lazimsiz pending order-i silin, movqeni ozunuz qerar verin."]
    lines += [f"      {o.line()}" for o in orphans]
    return lines


def main() -> None:
    import config.brokers as machine

    profile = machine.local()
    print(f"HESABDA HEC BIR BOTUN IDARE ETMEDIYI MOVQE / ORDER -- {profile.name}")
    for line in report_lines(fetch(profile), profile):
        print(line)


if __name__ == "__main__":
    main()
