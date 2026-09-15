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
