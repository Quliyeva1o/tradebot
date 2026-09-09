@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" run_live_nasdaq_orb.py --symbol XAUUSD --tp-r 3.0 --or-minutes 60 --risk-per-trade-pct 0.005
