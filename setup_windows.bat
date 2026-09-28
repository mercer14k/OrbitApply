@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD=py -3"
py -3 -c "import sys; assert (3,11) <= sys.version_info[:2] <= (3,14)" >nul 2>nul
if not errorlevel 1 goto :python_found
set "PYTHON_CMD=python"
python -c "import sys; assert (3,11) <= sys.version_info[:2] <= (3,14)" >nul 2>nul
if not errorlevel 1 goto :python_found
echo Python 3.11-3.14 was not found. Install 64-bit Python from https://www.python.org/downloads/windows/
echo Enable Add Python to PATH, then reopen this setup.
pause
exit /b 1

:python_found

where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama was not found. Install it from https://ollama.com/download/windows and rerun this setup.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" %PYTHON_CMD% -m venv .venv
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto :failed
ollama pull qwen3:8b
if errorlevel 1 goto :failed

echo.
echo Setup complete. Double-click run_windows.bat to start OrbitApply.
pause
exit /b 0

:failed
echo.
echo Setup did not finish. Review the error above.
pause
exit /b 1
