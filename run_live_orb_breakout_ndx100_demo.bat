@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" run_live_nasdaq_orb.py --symbol NDX100 --tp-r 3.0 --or-minutes 30 --risk-per-trade-pct 0.005
