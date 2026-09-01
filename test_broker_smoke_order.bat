@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" test_broker_smoke_order.py
