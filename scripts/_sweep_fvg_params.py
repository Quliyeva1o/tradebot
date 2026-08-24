"""Sweep min_gap and entry_mode for the midnight FVG strategy (SL=creating
candle wick, TP=2.5R fixed, no bias, no displacement), full ~4.1yr history.
"""
import importlib
import scripts.first_fvg_backtest as m

FIXED_TP_R = 2.5
MIN_GAPS = [1.0, 2.0, 3.0, 5.0, 8.0, 10.0]
ENTRY_MODES = ["touch", "confirmation"]

results = []
for entry_mode in ENTRY_MODES:
    for min_gap in MIN_GAPS:
        m.MIN_GAP_POINTS = min_gap
        m.ENTRY_MODE = entry_mode
        m.FIXED_TP_R = FIXED_TP_R
        trades = m.run_backtest()
        n = len(trades)
        wins = [t for t in trades if t.pnl_usd > 0]
        gp = sum(t.pnl_usd for t in wins)
        gl = abs(sum(t.pnl_usd for t in trades if t.pnl_usd <= 0))
        pf = gp / gl if gl > 0 else float("inf")
        net = sum(t.pnl_usd for t in trades)
        wr = len(wins) / n * 100 if n else 0
        results.append((entry_mode, min_gap, n, wr, pf, net))
        print(f"entry={entry_mode:12s} min_gap={min_gap:5.1f}  n={n:3d}  WR={wr:5.1f}%  PF={pf:5.2f}  net=${net:>10,.2f}")

print()
print("Sorted by net P&L:")
for r in sorted(results, key=lambda x: -x[5]):
    print(f"  entry={r[0]:12s} min_gap={r[1]:5.1f}  n={r[2]:3d}  WR={r[3]:5.1f}%  PF={r[4]:5.2f}  net=${r[5]:>10,.2f}")
