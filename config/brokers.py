"""Which broker THIS machine trades, and what that broker calls each symbol.

One repo, two machines, two accounts: since 2026-09-21 the VPS is logged into CFI and the
workstation into FundingPips. Both run the same launchers, because the launchers are in git --
so the broker's own ticker cannot live in them any more. CFI's gold is `XAUUSD_`, FundingPips'
is `XAUUSD`, and a launcher naming either one polls a symbol that does not exist on the other
machine. That is the exact failure deploy/preflight.py was written about: after the previous
broker change the bots polled `NAS100` for a week.

The machine already says which account it trades, in the one file that is deliberately not in
git: `.env`'s MT5_SERVER, which MT5Connector.connect() logs in with. Each broker's captured
specs file records the server it was captured on and, per symbol, that broker's own ticker.
So the whole mapping is data this repo already keeps:

    .env MT5_SERVER  ->  broker profile  ->  this broker's ticker for the name we use

Nothing here touches MT5 or places an order. A runner resolves its ticker BEFORE it connects
(its state files are named from the ticker, so the two brokers can never share one), then
verifies the terminal really is on this server once it has.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# The specs files live with the replay that captured them (scripts/capture_symbol_specs.py).
# They are broker profiles, not backtest data: server, tickers and contract facts.
SPECS_DIR = Path(__file__).resolve().parent.parent / "backtest" / "live_replay"
SPECS_FILES = {
    "fundingpips": SPECS_DIR / "symbol_specs.json",
    "cfi": SPECS_DIR / "symbol_specs_cfi.json",
}


class UnknownBrokerError(LookupError):
    """The configured server matches no captured broker profile."""


@dataclass(frozen=True)
class BrokerProfile:
    """One broker: the server its account lives on, and its name for each symbol we trade."""

    name: str
    server: str
    tickers: dict[str, str]  # this repo's symbol -> the broker's own ticker

    def ticker(self, symbol: str) -> str:
        """The name to send to MT5 for `symbol`.

        Raises:
            UnknownBrokerError: If this broker's captured specs do not list the symbol at all.
                Better a refusal at startup than a bot polling a nonexistent ticker for a week.
        """
        try:
            return self.tickers[symbol]
        except KeyError:
            raise UnknownBrokerError(
                f"{self.name} ({self.server}) has no symbol {symbol!r} -- "
                f"it trades {', '.join(sorted(self.tickers))}. "
                f"Recapture with scripts/capture_symbol_specs.py if the broker added it."
            ) from None

    def symbol(self, ticker: str) -> str:
        """This repo's name for one of the broker's tickers, or the ticker when it is not ours."""
        return next((s for s, t in self.tickers.items() if t == ticker), ticker)


@lru_cache(maxsize=1)
def profiles() -> dict[str, BrokerProfile]:
    """Every captured broker profile, keyed by short name."""
    out = {}
    for name, path in SPECS_FILES.items():
        raw = json.loads(path.read_text(encoding="utf-8"))
        out[name] = BrokerProfile(
            name=name,
            server=raw["server"],
            tickers={symbol: row.get("broker_symbol") or symbol
                     for symbol, row in raw["symbols"].items()},
        )
    return out


def for_server(server: str) -> BrokerProfile:
    """The profile captured on `server`.

    Raises:
        UnknownBrokerError: If no profile was captured on it.
    """
    for profile in profiles().values():
        if profile.server == server:
            return profile
    known = ", ".join(f"{p.server} ({p.name})" for p in profiles().values())
    raise UnknownBrokerError(
        f"no broker profile was captured on server {server!r}; known: {known}. "
        f"Run scripts/capture_symbol_specs.py on that terminal first."
    )


def local() -> BrokerProfile:
    """The broker this machine trades, from .env's MT5_SERVER.

    Read from the environment on every call rather than from Settings: Settings resolves its
    fields at import time, and MT5Connector.connect() calls load_dotenv() itself, so a module
    imported before the .env was loaded would otherwise answer with a stale server forever.

    Raises:
        UnknownBrokerError: If MT5_SERVER is unset or names no captured profile.
    """
    load_dotenv()
    server = os.getenv("MT5_SERVER", "")
    if not server:
        raise UnknownBrokerError(
            "MT5_SERVER is not set -- .env decides which broker this machine trades "
            "(see deploy/README.md). Copy .env.example and fill it in."
        )
    return for_server(server)
