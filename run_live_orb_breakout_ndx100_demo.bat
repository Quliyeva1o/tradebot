@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" run_live_nasdaq_orb.py --symbol US100_Spot --tp-r 4.0 --or-minutes 30 --scan-timeframe M5 --risk-per-trade-pct 0.005
