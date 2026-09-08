@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" run_live_nasdaq_orb.py --symbol XAUUSD --tp-r 4.0 --risk-per-trade-pct 0.005 --paper
