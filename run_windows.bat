@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Run setup_windows.bat first.
  pause
  exit /b 1
)

echo Keep this window open. In Chrome, visit http://127.0.0.1:8501
".venv\Scripts\python.exe" -m streamlit run launch.py --server.address 127.0.0.1 --server.port 8501 --server.headless false
if errorlevel 1 pause
