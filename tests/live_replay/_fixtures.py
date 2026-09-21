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

# The same instrument on the other broker: CFI quotes its indices' swap as money per lot per
# day in the account's deposit currency (mode 4), not as an annual percent -- and JP225 is
# quoted in JPY, which is what makes the missing FX conversion observable.
CFI_JP = SymbolSpec(symbol="JP225", point=0.01, tick_size=0.01, contract_size=500.0,
                    volume_min=0.01, volume_step=0.01, volume_max=100.0, profit_currency="JPY",
                    swap_mode=4, swap_long=-31.770, swap_short=-14.478, swap_rollover3days=5,
                    margin_rate=0.01, commission_per_lot_usd=0.0, broker_symbol="JPN225_SPOT")
