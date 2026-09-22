@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" run_live_first_fvg_window.py --symbol NDX100 --tp-r 3.0 --session-start 10:00 --c1-bars-before 1 --third-candle-before 11:00 --risk-per-trade-pct 0.0025
