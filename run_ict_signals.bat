@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m ict_lab.live >> logs\ict_signals.log 2>&1
