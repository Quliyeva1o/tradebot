@echo off
rem ICT A+ order bot, DEMO ORDERS on the CFI demo account (refuses to start on anything that is not a verified demo,
rem hedging account). Not registered as a Scheduled Task: enabling it is a decision (see ict_lab/execute.py).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m ict_lab.execute --loop --place-orders --feed cfi --symbols NDX100 GBPUSD --tp-r 2.0 --risk-per-trade-pct 0.0025
