@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m ict_lab.live --loop --feed cfi --silent >> logs\ict_signals_cfi.log 2>&1
