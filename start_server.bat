@echo off
REM ─────────────────────────────────────────────────────────────
REM  start_server.bat — Launch Staging Hub Flask Server
REM  Double-click this every day to start the server.
REM  Workers access: http://172.31.44.121:5000
REM ─────────────────────────────────────────────────────────────

cd /d "%~dp0"

REM ── Activate venv ──
if not exist venv\Scripts\activate.bat (
    echo ERROR: venv not found! Run setup_remote.bat first.
    pause
    exit /b 1
)
call venv\Scripts\activate.bat

REM ── Check required files ──
if not exist credentials.json (
    echo ERROR: credentials.json not found!
    echo Copy it into this folder and try again.
    pause
    exit /b 1
)
if not exist .env (
    echo ERROR: .env not found!
    echo Copy it into this folder and try again.
    pause
    exit /b 1
)

echo.
echo  Starting Staging Hub server (production mode)...
echo  Server will keep running after you disconnect RDP.
echo  Press Ctrl+C to stop.
echo.

python app.py --production
pause
