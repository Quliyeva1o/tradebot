#!/usr/bin/env python3
"""ONE-SHOT diagnostic: places the smallest possible REAL market order on the
DEMO account via MT5Broker (the exact same code path every run_live_*.py bot
uses to place real orders -- NOT PaperBroker), waits 10 seconds, then closes
it. Purpose: confirm the full Task Scheduler -> .bat -> python -> MT5Broker
-> broker path actually gets an order FILLED, before relying on it from an
always-on VPS.

Directly tests a known, previously-found blocker: on 2026-08-31, this
account's MT5 terminal-wide AutoTrading/AlgoTrading toggle was found OFF,
which silently rejects every real order with retcode=10027 "AutoTrading
disabled by client" (see project memory / SESSION_HANDOFF.md). Every
"trade_opened" event logged since then was tagged mode=paper -- meaning no
Demo bot has ever actually gotten a real fill confirmed. This script's whole
purpose is to surface that retcode (or confirm it's now fixed) directly,
rather than infer it from a strategy that may simply not have fired a signal
recently.

SAFETY -- same two-layer demo-account rail as every other real-order script
in this repo (run_live_demo.py, run_live_xauusd_orb.py, etc.): refuses to
run unless .env's MT5_ACCOUNT_TYPE=demo AND the connected account's own
MT5-reported trade_mode is ACCOUNT_TRADE_MODE_DEMO. Places a fixed 0.01-lot
XAUUSD BUY market order (no SL/TP -- deliberately naked, so a rejection here
can ONLY be about order routing itself, not stop-distance validation), with
no strategy logic involved at all.

Usage (same as any other Task Scheduler entry -- see
test_broker_smoke_order.bat):
    python test_broker_smoke_order.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).parent.resolve()))

import MetaTrader5 as mt5  # noqa: N813

from config.settings import Settings
from core.models import AccountInfo, OrderType
from execution.models import OrderRequest
from execution.mt5_broker import MT5Broker
from mt5.connector import MT5Connector
from risk.kill_switch import activate_kill_switch, is_trading_halted
from utils.logging import setup_logger

logger = setup_logger("test_broker_smoke_order", log_to_file=True)

SYMBOL = "XAUUSD"
VOLUME = 0.01
HOLD_SECONDS = 10


class DemoAccountRequiredError(RuntimeError):
    """Raised when this script is not explicitly and verifiably pointed at a demo account."""


def _ensure_explicit_demo_configuration() -> None:
    """Identical gate to run_live_demo.py -- see that module for the full rationale."""
    account_type = Settings.load().MT5_ACCOUNT_TYPE.strip().lower()
    if account_type != "demo":
        raise DemoAccountRequiredError(
            f"MT5_ACCOUNT_TYPE must be explicitly set to 'demo' in .env to run "
            f"test_broker_smoke_order.py (got {account_type!r}). Refusing to start."
        )


def _ensure_demo_trade_mode(account_info: AccountInfo) -> None:
    """Identical gate to run_live_demo.py -- see that module for the full rationale."""
    if account_info.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        raise DemoAccountRequiredError(
            f"Connected MT5 account does not report a DEMO trade_mode "
            f"(got {account_info.trade_mode!r}, expected "
            f"{mt5.ACCOUNT_TRADE_MODE_DEMO} = ACCOUNT_TRADE_MODE_DEMO). "
            "Refusing to trade -- this account may be LIVE."
        )


def main() -> None:
    print(f"[{SYMBOL}] Broker order smoke test starting -- REAL order via MT5Broker, volume={VOLUME}")

    try:
        _ensure_explicit_demo_configuration()
    except DemoAccountRequiredError as exc:
        logger.critical("DEMO-ACCOUNT SAFETY RAIL TRIPPED (config): %s", exc)
        print(f"REFUSING TO START: {exc}")
        sys.exit(1)

    connector = MT5Connector()
    broker = MT5Broker(connector=connector)
    if not broker.connect():
        logger.error("Could not connect to MT5.")
        print("FAILED: could not connect to MT5 terminal.")
        sys.exit(1)

    try:
        account_info = broker.get_account_info()
        try:
            _ensure_demo_trade_mode(account_info)
        except DemoAccountRequiredError as exc:
            logger.critical("DEMO-ACCOUNT SAFETY RAIL TRIPPED (MT5 account): %s", exc)
            activate_kill_switch(f"test_broker_smoke_order.py: {exc}")
            print(f"REFUSING TO TRADE: {exc}")
            sys.exit(1)

        print(f"Connected: currency={account_info.currency} trade_mode={account_info.trade_mode} "
              f"balance={account_info.balance:.2f} equity={account_info.equity:.2f}")
        terminal_info = mt5.terminal_info()
        print(f"Terminal trade_allowed (AutoTrading toggle): {terminal_info.trade_allowed if terminal_info else 'UNKNOWN'}")

        # Every real-order run_live_*.py script refuses to trade while the
        # kill-switch is active; this diagnostic places a REAL order too
        # (see module docstring) and must not be the one exception that
        # bypasses it -- an operator running this smoke test to debug a
        # broker/terminal issue could otherwise place a live order while
        # trading is supposed to be halted account-wide.
        if is_trading_halted():
            logger.error("Kill-switch is active; refusing to place a real order.")
            print("REFUSING TO TRADE: kill-switch is active (risk/kill_switch.flag exists).")
            sys.exit(1)

        order = OrderRequest(
            symbol=SYMBOL,
            order_type=OrderType.BUY_MARKET,
            volume=VOLUME,
            comment="smoke_test",
        )
        print(f"Placing REAL market order: {SYMBOL} BUY {VOLUME} lot...")
        result = broker.place_order(order)
        print(f"ORDER RESULT: success={result.success} retcode={result.retcode} "
              f"comment={result.comment!r} order_id={result.order_id} price={result.price}")

        if not result.success:
            print("\n>>> ORDER REJECTED. This IS the answer to the question -- broker/terminal "
                  "blocked real order routing. See retcode/comment above (10027 = AutoTrading "
                  "disabled by client is the known suspect).")
            return

        print(f"\n>>> ORDER FILLED. Holding for {HOLD_SECONDS}s, then closing...")
        time.sleep(HOLD_SECONDS)

        close_result = broker.close_position(result.position_id)
        print(f"CLOSE RESULT: success={close_result.success} retcode={close_result.retcode} "
              f"comment={close_result.comment!r} price={close_result.price}")
        if close_result.success:
            print("\n>>> Position opened AND closed successfully. Real order routing works.")
        else:
            print("\n>>> Position OPENED but FAILED TO CLOSE automatically -- "
                  "close it manually in the MT5 terminal.")
    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()
