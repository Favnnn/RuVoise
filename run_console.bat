@echo off
rem Launch RuVoise with a console window (for diagnostics).
cd /d "%~dp0"
".venv\Scripts\python.exe" -X utf8 "%~dp0run.py"
pause
