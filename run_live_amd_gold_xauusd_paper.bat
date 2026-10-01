@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" run_live_amd.py --symbol XAUUSD --tp-r 2.0 --min-fvg 0.5 --spread-floor 0.15 --risk-per-trade-pct 0.005 --paper
