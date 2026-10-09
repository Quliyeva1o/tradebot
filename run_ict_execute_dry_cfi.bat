@echo off
rem ICT A+ order bot, DRY RUN: scans the CFI terminal and prints/logs what it would place; sends nothing.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m ict_lab.execute --loop --feed cfi --symbols NDX100 GBPUSD --tp-r 2.0 --risk-per-trade-pct 0.0025
