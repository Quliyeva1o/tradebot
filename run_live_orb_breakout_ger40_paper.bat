@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" run_live_nasdaq_orb.py --symbol GER30_SPOT --tp-r 4.0 --or-minutes 30 --risk-per-trade-pct 0.005 --paper
