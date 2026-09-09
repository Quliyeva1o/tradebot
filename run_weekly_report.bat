@echo off
cd /d "%~dp0"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set STAMP=%%i
".venv\Scripts\python.exe" -m scripts.live_vs_backtest_report --days 30 > "logs\weekly_report_%STAMP%.txt" 2>&1
