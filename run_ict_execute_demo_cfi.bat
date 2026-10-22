@echo off
rem ICT A+ order bot, DEMO ORDERS on the CFI demo account (refuses to start on anything that is not a verified demo,
rem hedging account). US100 (NDX100) only, on the user's call of 2026-10-09; registered as the ict_exec_cfi task.
rem 2026-10-22: the ict_exec_cfi task is DISABLED on the user's call -- ICT runs on paper only (ict_signals_cfi).
rem GBPUSD is not in the CFI broker profile (config/brokers.py), so execute.py refuses to start with it.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m ict_lab.execute --loop --place-orders --feed cfi --symbols NDX100 --tp-r 2.0 --risk-per-trade-pct 0.0025
