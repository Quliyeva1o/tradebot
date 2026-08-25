@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" run_live_midnight_fvg.py --symbol NAS100 --timeframe M1 --risk-per-trade-pct 0.0005
