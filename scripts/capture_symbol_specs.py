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
