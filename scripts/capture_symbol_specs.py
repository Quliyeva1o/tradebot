"""Snapshot the replayed symbols' contract, lot, swap and margin facts from MT5 (READ-ONLY).

Never places or modifies anything: symbol_info(), order_calc_profit() and order_calc_margin()
only. Run it on the workstation with the terminal logged into the account being captured.

The default run captures FundingPips-Trial under this repo's own symbol names. A second broker
names the same instruments differently (CFI calls NDX100 "US100_Spot"), so --symbols takes
OURS=THEIRS pairs and records each broker ticker in the file: the replay keys everything by our
name and reads the broker's own history CSV.

Usage:
    python -m scripts.capture_symbol_specs
    python -m scripts.capture_symbol_specs --symbols XAUUSD=XAUUSD_,NDX100=US100_Spot \
        --commission none --out backtest/live_replay/symbol_specs_cfi.json
"""

from __future__ import annotations

import argparse
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


def _price(symbol: str, info) -> float:
    """The ask, or the last M1 close when the market is shut and the tick reads 0.0.

    order_calc_profit/margin both need a live-ish price, and a weekend capture otherwise
    divides by zero on every symbol whose session is closed.
    """
    tick = mt5.symbol_info_tick(symbol)
    if tick is not None and tick.ask > 0:
        return float(tick.ask)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1)
    if rates is None or not len(rates):
        raise RuntimeError(f"{symbol}: no tick and no M1 bar to price the capture from")
    return float(rates[-1]["close"])


def capture(symbol: str, commission: float = 0.0) -> dict:
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"{symbol} is not available: {mt5.last_error()}")
    info = mt5.symbol_info(symbol)
    price = _price(symbol, info)
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
        commission_per_lot_usd=commission,
        price_at_capture=price, usd_per_unit_at_capture=usd_per_unit,
    )


def parse_symbols(text: str) -> dict[str, str]:
    """"OURS=THEIRS,..." -> {ours: theirs}; a bare name maps to itself."""
    pairs = [part.split("=", 1) if "=" in part else [part, part]
             for part in (p.strip() for p in text.split(",")) if part]
    return {ours: theirs for ours, theirs in pairs}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default=",".join(SYMBOLS),
                        help="comma-separated OURS=THEIRS pairs (default: this repo's six, as named here)")
    parser.add_argument("--commission", default="default",
                        help="'default' = this repo's recorded FundingPips rates, 'none' = no "
                             "commission on any symbol, or OURS=USD_PER_LOT pairs")
    parser.add_argument("--out", default=str(SPECS_FILE))
    args = parser.parse_args()

    symbols = parse_symbols(args.symbols)
    if args.commission == "default":
        commissions = COMMISSION_PER_LOT_USD
    elif args.commission == "none":
        commissions = {}
    else:
        commissions = {k: float(v) for k, v in parse_symbols(args.commission).items()}

    connector = MT5Connector()
    if not connector.connect():
        raise SystemExit("Could not connect to MT5; open the terminal and log in first.")
    try:
        account = mt5.account_info()
        payload = {
            "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "account": account.login, "server": account.server,
            "symbols": {ours: dict(capture(theirs, commissions.get(ours, 0.0)),
                                   broker_symbol=theirs)
                        for ours, theirs in symbols.items()},
        }
    finally:
        connector.disconnect()
    out = Path(args.out)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
