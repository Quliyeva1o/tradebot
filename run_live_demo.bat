@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" run_live_demo_with_crash_alert.py --symbol USTEC --timeframe M5
