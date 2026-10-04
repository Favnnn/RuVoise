@echo off
rem First install: creates the virtual environment and downloads dependencies.
rem NOTE: --no-cache-dir is intentional (a corrupted pip cache was found on this PC).
cd /d "%~dp0"
where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
".venv\Scripts\python.exe" -m pip install --no-cache-dir -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installation FAILED. Check your internet connection and run this file again.
) else (
  echo.
  echo Installation complete. Run start.bat to launch RuVoise.
)
pause
