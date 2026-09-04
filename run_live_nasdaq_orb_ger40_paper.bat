@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" run_live_nasdaq_orb.py --symbol GER40 --tp-r 3.0 --risk-per-trade-pct 0.01 --paper
