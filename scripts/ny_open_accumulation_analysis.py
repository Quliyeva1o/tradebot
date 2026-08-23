"""One-off SMC intraday analysis: "NY-Open Akkumulyasiya Sindirmasi + Retest"
for USTEC (NAS100), applied to the most recent completed NY trading session
in available data (2026-08-21 -- 2026-08-23 is a Sunday, no session).

Follows the user's exact 5-step procedure. Uses real MT5 (MetaQuotes-Demo)
USTEC M5 data for HTF structure/liquidity/premium-discount, and M1 data for
the 1-minute accumulation/engulf/retest detail.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
BROKER_TZ = ZoneInfo("Europe/Bucharest")
UTC = ZoneInfo("UTC")

ANALYSIS_DATE = date(2026, 8, 21)  # most recent completed NY trading day


def load(path):
    bars = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            naive = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            broker_local = naive.replace(tzinfo=BROKER_TZ)
            true_utc = broker_local.astimezone(UTC)
            ny_ts = true_utc.astimezone(NY)
            bars.append(
                {"ts": ny_ts, "open": float(row["open"]), "high": float(row["high"]),
                 "low": float(row["low"]), "close": float(row["close"])}
            )
    bars.sort(key=lambda b: b["ts"])
    return bars


m5 = load("data/history/USTEC_M5.csv")
m1 = load("data/history/USTEC_M1.csv")

by_date_m5 = defaultdict(list)
for b in m5:
    by_date_m5[b["ts"].date()].append(b)
trading_days = sorted(by_date_m5.keys())

by_date_m1 = defaultdict(list)
for b in m1:
    by_date_m1[b["ts"].date()].append(b)

idx = trading_days.index(ANALYSIS_DATE)
prev_day = trading_days[idx - 1]

print("=" * 70)
print(f"ANALIZ TARIXI: {ANALYSIS_DATE} (son tam bağlanmış NY sessiyası)")
print(f"Əvvəlki gün: {prev_day}")
print("=" * 70)

# ---------- STEP 1a: Daily structure (BOS/CHoCH) ----------
daily = []
for d in trading_days:
    if d > prev_day:
        break
    bars = by_date_m5[d]
    daily.append({"date": d, "high": max(b["high"] for b in bars), "low": min(b["low"] for b in bars),
                   "close": bars[-1]["close"], "open": bars[0]["open"]})

lookback = daily[-20:]
print("\n--- STEP 1a: Daily structure (son 20 gün) ---")
for d in lookback[-10:]:
    print(f"  {d['date']}  O={d['open']:.1f} H={d['high']:.1f} L={d['low']:.1f} C={d['close']:.1f}")

# 3-bar fractal swings on daily
swing_highs, swing_lows = [], []
for i in range(1, len(lookback) - 1):
    h, l = lookback[i]["high"], lookback[i]["low"]
    if h > lookback[i-1]["high"] and h > lookback[i+1]["high"]:
        swing_highs.append((lookback[i]["date"], h))
    if l < lookback[i-1]["low"] and l < lookback[i+1]["low"]:
        swing_lows.append((lookback[i]["date"], l))
print("Swing highs:", swing_highs)
print("Swing lows:", swing_lows)

# ---------- STEP 1b: Liquidity (PDH/PDL, Asia, London) ----------
prev_bars = by_date_m5[prev_day]
cash = [b for b in prev_bars if time(9, 30) <= b["ts"].time() < time(16, 0)]
pdh = max(b["high"] for b in cash)
pdl = min(b["low"] for b in cash)
print(f"\n--- STEP 1b: PDH={pdh:.2f}  PDL={pdl:.2f} ---")

# Asia session: prev_day 20:00 -> analysis_date 00:00 NY
asia_bars = [b for b in prev_bars if b["ts"].time() >= time(20, 0)]
asia_bars += [b for b in by_date_m1.get(ANALYSIS_DATE, []) if b["ts"].time() < time(0, 0)]  # none, placeholder
if asia_bars:
    asia_high = max(b["high"] for b in asia_bars)
    asia_low = min(b["low"] for b in asia_bars)
    print(f"Asia (prev 20:00-00:00 NY): H={asia_high:.2f} L={asia_low:.2f}")
else:
    asia_high = asia_low = None
    print("Asia session: data yoxdur")

# London session: analysis_date 02:00-05:00 NY
london_bars = [b for b in by_date_m5[ANALYSIS_DATE] if time(2, 0) <= b["ts"].time() < time(5, 0)]
if london_bars:
    london_high = max(b["high"] for b in london_bars)
    london_low = min(b["low"] for b in london_bars)
    print(f"London (02:00-05:00 NY): H={london_high:.2f} L={london_low:.2f}")
else:
    london_high = london_low = None
    print("London session: data yoxdur")

# Pre-09:30 bars to check if Asia/London liquidity already swept
pre_930 = [b for b in by_date_m5[ANALYSIS_DATE] if b["ts"].time() < time(9, 30)]
pre_930_high = max(b["high"] for b in pre_930) if pre_930 else None
pre_930_low = min(b["low"] for b in pre_930) if pre_930 else None
print(f"09:30-dan əvvəl (00:00-09:25) high/low: H={pre_930_high:.2f} L={pre_930_low:.2f}")
if asia_high:
    print(f"  Asia high alınıb (swept)?  {pre_930_high >= asia_high}")
    print(f"  Asia low alınıb (swept)?   {pre_930_low <= asia_low}")
if london_high:
    print(f"  London high alınıb (swept)? {pre_930_high >= london_high}")
    print(f"  London low alınıb (swept)?  {pre_930_low <= london_low}")
print(f"  PDH alınıb? {pre_930_high >= pdh}   PDL alınıb? {pre_930_low <= pdl}")

# ---------- STEP 1c: Premium/Discount ----------
range_window = daily[-10:]
range_high = max(d["high"] for d in range_window)
range_low = min(d["low"] for d in range_window)
midpoint = (range_high + range_low) / 2
bar_930 = next(b for b in by_date_m5[ANALYSIS_DATE] if b["ts"].time() == time(9, 30))
open_930 = bar_930["open"]
print(f"\n--- STEP 1c: Premium/Discount (son 10 gün range: {range_low:.2f}-{range_high:.2f}, mid={midpoint:.2f}) ---")
print(f"09:30 açılış qiyməti: {open_930:.2f}  -> {'PREMIUM (üst yarı)' if open_930 > midpoint else 'DISCOUNT (alt yarı)'}")

# ---------- STEP 2: Accumulation on M1 from 09:30 ----------
print("\n--- STEP 2: Akkumulyasiya (1m, 09:30-dan) ---")
day_m1 = [b for b in by_date_m1[ANALYSIS_DATE] if b["ts"].time() >= time(9, 30)]
for b in day_m1[:30]:
    body = abs(b["close"] - b["open"])
    color = "YAŞIL" if b["close"] > b["open"] else ("QIRMIZI" if b["close"] < b["open"] else "DOJI")
    print(f"  {b['ts'].time()} O={b['open']:.2f} H={b['high']:.2f} L={b['low']:.2f} C={b['close']:.2f} body={body:.1f} {color}")

print("\n--- Sliding-window compression scan (4-8 candle qruplari, 09:30-dan) ---")
for start_i in range(0, 15):
    for n in range(4, 9):
        group = day_m1[start_i:start_i+n]
        if len(group) < n:
            continue
        grp_high = max(b["high"] for b in group)
        grp_low = min(b["low"] for b in group)
        span = grp_high - grp_low
        print(f"  start={day_m1[start_i]['ts'].time()} n={n} range={span:.1f} (H={grp_high:.2f} L={grp_low:.2f})")
