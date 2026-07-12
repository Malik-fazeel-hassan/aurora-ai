@echo off
setlocal
cd /d "%~dp0"

echo.
echo ========================================
echo   Aurora AI - Windows starter
echo ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating virtual environment...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)

echo [2/4] Activating venv...
call ".venv\Scripts\activate.bat"

echo [3/4] Ensuring packages...
python -m pip install -q -r requirements.txt

if not exist "data" mkdir data
if not exist "workspace" mkdir workspace
if not exist "data\settings.json" if exist "data\settings.example.json" copy /Y "data\settings.example.json" "data\settings.json" >nul
if not exist "data\mcp_servers.json" if exist "data\mcp_servers.example.json" copy /Y "data\mcp_servers.example.json" "data\mcp_servers.json" >nul

set PYTHONPATH=%cd%\backend;%cd%
set HOST=127.0.0.1
set PORT=7860

echo [4/4] Starting server...
echo.
echo   Open Chrome and go to:
echo     http://127.0.0.1:7860
echo     http://localhost:7860
echo.
echo   KEEP THIS WINDOW OPEN.
echo   Press CTRL+C to stop Aurora.
echo.

python -m uvicorn backend.main:app --host 127.0.0.1 --port 7860 --log-level info
if errorlevel 1 (
  echo.
  echo Server failed. Trying alternate bind 0.0.0.0 ...
  python -m uvicorn backend.main:app --host 0.0.0.0 --port 7860 --log-level info
)

echo.
echo Aurora stopped.
pause
