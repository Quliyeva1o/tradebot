@echo off
rem ICT A+ order bot, DEMO ORDERS on the CFI demo account (refuses to start on anything that is not a verified demo,
rem hedging account). US100 (NDX100) only, on the user's call of 2026-10-09; registered as the ict_exec_cfi task.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m ict_lab.execute --loop --place-orders --feed cfi --symbols NDX100 --tp-r 2.0 --risk-per-trade-pct 0.0025
