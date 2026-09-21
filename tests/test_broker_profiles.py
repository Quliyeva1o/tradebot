"""Which broker a machine trades, and what it calls each symbol.

Since 2026-09-21 one launcher set runs on two accounts -- the VPS on CFI, the workstation on
FundingPips -- and the ticker comes from the machine's .env instead of from the .bat. That put
the whole "after a broker change the bots polled a symbol that did not exist for a week" failure
class (deploy/preflight.py's docstring) behind these checks.
"""

from pathlib import Path

import pytest

import config.brokers as machine
from backtest.live_replay.configs import parse_bat

REPO = Path(__file__).parent.parent


def test_both_accounts_are_captured_with_their_own_names_for_gold() -> None:
    """The one symbol that actually places real orders, under each broker's own ticker."""
    assert machine.profiles()["cfi"].server == "CFI11-Demo"
    assert machine.profiles()["cfi"].ticker("XAUUSD") == "XAUUSD_"
    assert machine.profiles()["fundingpips"].server == "FundingPips-Trial"
    assert machine.profiles()["fundingpips"].ticker("XAUUSD") == "XAUUSD"


def test_every_deployed_launcher_resolves_on_both_accounts() -> None:
    """The point of the whole mechanism: the same .bat has to run on either machine. A symbol
    one broker does not list would leave that machine's bot polling nothing -- silently, for as
    long as nobody reads the log."""
    symbols = {parse_bat(p).symbol for p in sorted(REPO.glob("run_live_orb_*.bat"))}
    assert symbols, "no launchers found"

    for profile in machine.profiles().values():
        for symbol in sorted(symbols):
            assert profile.ticker(symbol), f"{profile.name} cannot name {symbol}"


def test_a_symbol_the_broker_does_not_list_is_refused_not_guessed() -> None:
    with pytest.raises(machine.UnknownBrokerError, match="NAS100"):
        machine.profiles()["cfi"].ticker("NAS100")


def test_an_uncaptured_server_is_refused_with_the_ones_that_are_known() -> None:
    with pytest.raises(machine.UnknownBrokerError, match="CFI11-Demo"):
        machine.for_server("FXTM-Demo02")


def test_the_machines_broker_comes_from_env(monkeypatch) -> None:
    monkeypatch.setattr(machine, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("MT5_SERVER", "FundingPips-Trial")
    assert machine.local().name == "fundingpips"

    monkeypatch.setenv("MT5_SERVER", "CFI11-Demo")
    assert machine.local().name == "cfi"


def test_a_machine_that_does_not_say_which_account_it_trades_is_refused(monkeypatch) -> None:
    """Refusing beats defaulting: a wrong guess here trades the wrong account."""
    monkeypatch.setattr(machine, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("MT5_SERVER", raising=False)
    with pytest.raises(machine.UnknownBrokerError, match="MT5_SERVER"):
        machine.local()


def test_a_terminal_on_another_account_stops_the_bot(monkeypatch) -> None:
    """Every ticker and every state-file name is derived from .env's server. If the terminal is
    somewhere else, all of them are wrong -- so nothing may run."""
    import mt5.connector as connector

    profile = machine.profiles()["cfi"]
    monkeypatch.setattr(connector.mt5, "account_info",
                        lambda: type("A", (), {"server": "FundingPips-Trial"})(), raising=False)
    with pytest.raises(connector.WrongBrokerError, match="FundingPips-Trial"):
        connector.ensure_logged_into(profile)

    monkeypatch.setattr(connector.mt5, "account_info",
                        lambda: type("A", (), {"server": "CFI11-Demo"})(), raising=False)
    connector.ensure_logged_into(profile)  # matches: no refusal


def test_a_terminal_that_cannot_be_asked_is_not_assumed_to_be_right(monkeypatch) -> None:
    import mt5.connector as connector

    monkeypatch.setattr(connector.mt5, "account_info", lambda: None, raising=False)
    with pytest.raises(connector.WrongBrokerError):
        connector.ensure_logged_into(machine.profiles()["cfi"])


def test_the_ticker_a_runner_uses_is_the_machines_own(monkeypatch) -> None:
    """resolve_ticker() runs before MT5 is connected, because the bot's state files are named
    from what it returns -- CFI's XAUUSD_ files and FundingPips' XAUUSD ones never mix."""
    import mt5.connector as connector

    monkeypatch.setattr(machine, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("MT5_SERVER", "CFI11-Demo")
    assert connector.resolve_ticker("XAUUSD") == (machine.profiles()["cfi"], "XAUUSD_")

    monkeypatch.setenv("MT5_SERVER", "FundingPips-Trial")
    assert connector.resolve_ticker("NDX100") == (machine.profiles()["fundingpips"], "NDX100")
