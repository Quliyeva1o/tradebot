@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" run_live_xauusd_orb.py --symbol GER40 --timeframe M15 --risk-per-trade-pct 0.005 --paper
