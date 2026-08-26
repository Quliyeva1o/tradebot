@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" run_live_sr_bias.py --symbol NAS100 --timeframe M30 --risk-per-trade-pct 0.0025
