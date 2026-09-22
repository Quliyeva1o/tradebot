"""What is on an account that no Demo bot of that account manages (scripts/account_orphans.py).

The fixtures are the FundingPips account as read on 2026-09-22, a day after the VPS stood its
FundingPips Demo bots down and moved to CFI: one SPX500 trade still open, the reverse order resting
at its stop, and the NDX100 reverse order whose trade had already reached its target.
"""

from types import SimpleNamespace

import MetaTrader5
import pytest

from config.brokers import BrokerProfile
from execution.stop_and_reverse import reverse_comment
from scripts import account_orphans
from scripts.account_orphans import find_orphans, owned_symbols, report_lines

TAG = "setup_nasdaq_orb_m1"
FP = BrokerProfile(name="fundingpips", server="FundingPips-Trial",
                   tickers={"XAUUSD": "XAUUSD", "NDX100": "NDX100", "SPX500": "SPX500"})
CFI = BrokerProfile(name="cfi", server="CFI11-Demo",
                    tickers={"XAUUSD": "XAUUSD_", "NDX100": "US100_Spot", "SPX500": "US500_SPOT"})


def _position(ticket, symbol, comment=f"{TAG}__d4afb089"):
    return SimpleNamespace(ticket=ticket, symbol=symbol, type=0, volume=0.14, price_open=7645.77,
                           comment=comment)


def _order(ticket, symbol, comment, type_=5):
    return SimpleNamespace(ticket=ticket, symbol=symbol, type=type_, volume_current=0.09,
                           price_open=29299.75, comment=comment)


SPX_TRADE = _position(13140575, "SPX500")
SPX_REVERSE = _order(13140576, "SPX500", reverse_comment(TAG, "13140575"))
NDX_REVERSE = _order(13011609, "NDX100", reverse_comment(TAG, "13011608"))


def test_the_fundingpips_account_as_left_on_2026_09_21_is_all_orphaned() -> None:
    # nothing is rostered on FundingPips, so every one of the three is on its own
    found = find_orphans([SPX_TRADE], [SPX_REVERSE, NDX_REVERSE], owned={})
    assert sorted(o.ticket for o in found) == [13011609, 13140575, 13140576]
    assert {o.kind for o in found} == {"movqe", "pending"}


def test_on_a_rostered_symbol_a_reverse_order_needs_its_own_trade_open() -> None:
    owned = {"SPX500": "OrbBreakout_SPX500_Demo", "NDX100": "OrbBreakout_NDX100_Demo"}
    found = find_orphans([SPX_TRADE], [SPX_REVERSE, NDX_REVERSE], owned)
    [orphan] = found
    assert orphan.ticket == 13011609           # its trade 13011608 closed at target
    assert "aciq deyil" in orphan.why


def test_a_rostered_bots_own_trade_and_ordinary_pending_order_are_not_flagged() -> None:
    limit = _order(900, "US100_Spot", "setup_fvg_window_20260922", type_=2)
    trade = _position(901, "XAUUSD_", "setup_nasdaq_orb_m1__0a1b2c3d")
    owned = {"XAUUSD_": "OrbBreakoutwf_XAUUSD_Demo", "US100_Spot": "FvgWindow_NDX100_Demo"}
    assert find_orphans([trade], [limit], owned) == []


def test_owned_symbols_follow_the_roster_for_this_broker_only(tmp_path) -> None:
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "demo_roster.txt").write_text(
        "# comment\nOrbBreakoutwf_XAUUSD_Demo  cfi  # gold\nFvgWindow_NDX100_Demo  cfi\n", encoding="utf-8")
    for name, symbol in (("run_live_orb_breakoutwf_xauusd_demo.bat", "XAUUSD"),
                         ("run_live_fvg_window_ndx100_demo.bat", "NDX100"),
                         ("run_live_orb_breakout_spx500_demo.bat", "SPX500")):   # not rostered
        (tmp_path / name).write_text(f'".venv\\Scripts\\python.exe" x.py --symbol {symbol} --tp-r 3\n',
                                     encoding="utf-8")
    assert owned_symbols(CFI, tmp_path) == {"XAUUSD_": "OrbBreakoutwf_XAUUSD_Demo",
                                            "US100_Spot": "FvgWindow_NDX100_Demo"}
    assert owned_symbols(FP, tmp_path) == {}


def test_a_failed_listing_is_an_error_not_a_clean_account(monkeypatch) -> None:
    monkeypatch.setattr(MetaTrader5, "positions_get", lambda: None, raising=False)
    monkeypatch.setattr(MetaTrader5, "orders_get", lambda: (), raising=False)
    monkeypatch.setattr(MetaTrader5, "last_error", lambda: (-10004, "No IPC connection"), raising=False)
    with pytest.raises(RuntimeError, match="positions/orders"):
        account_orphans.read(FP)


def test_the_report_says_so_either_way() -> None:
    assert "yoxdur" in report_lines([], CFI)[0]
    lines = report_lines(find_orphans([SPX_TRADE], [], owned={}), FP)
    assert "DIQQET" in lines[0] and "13140575" in lines[-1]
