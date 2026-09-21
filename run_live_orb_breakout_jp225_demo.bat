@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" run_live_nasdaq_orb.py --symbol JPN225_SPOT --tp-r 4.0 --or-minutes 15 --risk-per-trade-pct 0.005
