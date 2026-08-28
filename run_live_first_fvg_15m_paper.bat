@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" run_live_first_fvg_15m.py --symbol NAS100 --timeframe M15 --risk-per-trade-pct 0.0025 --paper
