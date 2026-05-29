@echo off
REM ═══════════════════════════════════════════════════════════
REM  install_nssm_service.bat — Staging Hub Production Setup
REM  RIGHT-CLICK → Run as Administrator
REM
REM  Python  : C:\Users\hp\AppData\Local\Programs\Python\Python313\python.exe
REM  App     : C:\Users\hp\Desktop\staging_main\app.py
REM  Service : StagingHub
REM ═══════════════════════════════════════════════════════════

cd /d "%~dp0"

REM ── Admin check ─────────────────────────────────────────────
net session >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo.
    echo  ERROR: Must be run as Administrator!
    echo  Right-click this file → Run as administrator
    echo.
    pause
    exit /b 1
)

REM ── Fixed Paths (no venv — using global Python 3.13) ────────
set APP_DIR=C:\Users\hp\Desktop\staging_main
set PYTHON_EXE=C:\Users\hp\AppData\Local\Programs\Python\Python313\python.exe
set NSSM_EXE=%APP_DIR%\nssm.exe
set SERVICE_NAME=StagingHub
set LOG_DIR=%APP_DIR%\logs

echo.
echo  ═══════════════════════════════════════════════════════
echo    Staging Hub — Production Service Installer
echo  ═══════════════════════════════════════════════════════
echo   Python   : %PYTHON_EXE%
echo   App Dir  : %APP_DIR%
echo   Service  : %SERVICE_NAME%
echo   Logs     : %LOG_DIR%
echo  ═══════════════════════════════════════════════════════
echo.

REM ── Verify python.exe exists ─────────────────────────────────
if not exist "%PYTHON_EXE%" (
    echo  ERROR: python.exe not found at expected path!
    echo  Expected: %PYTHON_EXE%
    echo  Run "where python" in PowerShell to find your path.
    echo.
    pause
    exit /b 1
)

REM ── Verify nssm.exe exists ───────────────────────────────────
if not exist "%NSSM_EXE%" (
    echo  ERROR: nssm.exe not found!
    echo.
    echo  1. Go to: https://nssm.cc/download
    echo  2. Download nssm-2.24.zip
    echo  3. Extract it
    echo  4. Copy win64\nssm.exe into:
    echo       %APP_DIR%\
    echo  5. Run this script again.
    echo.
    pause
    exit /b 1
)

REM ── Create logs directory ────────────────────────────────────
echo  [1/5] Creating logs directory...
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
echo         OK: %LOG_DIR%

REM ── Install Waitress (already installed but ensure it) ───────
echo  [2/5] Verifying Waitress is installed...
"%PYTHON_EXE%" -m pip install waitress --quiet
echo         OK: waitress ready

REM ── Remove old service if exists ────────────────────────────
echo  [3/5] Removing old service (if exists)...
"%NSSM_EXE%" stop %SERVICE_NAME% >nul 2>&1
"%NSSM_EXE%" remove %SERVICE_NAME% confirm >nul 2>&1
echo         OK: old service cleared

REM ── Install the Windows Service ─────────────────────────────
echo  [4/5] Installing Windows Service...

REM Set executable = python.exe
"%NSSM_EXE%" install %SERVICE_NAME% "%PYTHON_EXE%"

REM Arguments: run waitress as a Python module
"%NSSM_EXE%" set %SERVICE_NAME% AppParameters "-m waitress --host=0.0.0.0 --port=5000 --threads=8 app:app"

REM Working directory = project root (so "app:app" resolves correctly)
"%NSSM_EXE%" set %SERVICE_NAME% AppDirectory "%APP_DIR%"

REM Environment variables
"%NSSM_EXE%" set %SERVICE_NAME% AppEnvironmentExtra "STAGING_PRODUCTION=true" "PYTHONIOENCODING=utf-8" "PYTHONUTF8=1"

REM Log stdout and stderr to files
"%NSSM_EXE%" set %SERVICE_NAME% AppStdout "%LOG_DIR%\stdout.log"
"%NSSM_EXE%" set %SERVICE_NAME% AppStderr "%LOG_DIR%\stderr.log"
"%NSSM_EXE%" set %SERVICE_NAME% AppStdoutCreationDisposition 4
"%NSSM_EXE%" set %SERVICE_NAME% AppStderrCreationDisposition 4

REM Rotate logs at 10 MB so they don't grow forever
"%NSSM_EXE%" set %SERVICE_NAME% AppRotateFiles 1
"%NSSM_EXE%" set %SERVICE_NAME% AppRotateBytes 10485760

REM Auto-restart on crash after 5 seconds
"%NSSM_EXE%" set %SERVICE_NAME% AppExit Default Restart
"%NSSM_EXE%" set %SERVICE_NAME% AppRestartDelay 5000

REM Service display name and description
"%NSSM_EXE%" set %SERVICE_NAME% DisplayName "Staging Hub — LNTZ Worker Portal"
"%NSSM_EXE%" set %SERVICE_NAME% Description "Production WSGI server (Waitress) for the LNTZ Staging warehouse scanning system. Manages HMS sync, POC reporting, and daily backups."

REM Start automatically on every Windows boot
"%NSSM_EXE%" set %SERVICE_NAME% Start SERVICE_AUTO_START

if %ERRORLEVEL% neq 0 (
    echo.
    echo  ERROR: NSSM configuration failed!
    pause
    exit /b 1
)
echo         OK: service configured

REM ── Start the service now ────────────────────────────────────
echo  [5/5] Starting service...
"%NSSM_EXE%" start %SERVICE_NAME%

REM Wait 4 seconds for startup
timeout /t 4 /nobreak >nul

REM Show current status
echo.
echo  Status:
"%NSSM_EXE%" status %SERVICE_NAME%

echo.
echo  ═══════════════════════════════════════════════════════
echo    ✅ INSTALLATION COMPLETE!
echo  ═══════════════════════════════════════════════════════
echo.
echo   Worker Portal : http://localhost:5000
echo   Dashboard     : http://localhost:5000/dashboard
echo   Network URL   : http://10.244.3.154:5000
echo.
echo   Logs (errors and output) saved to:
echo     %LOG_DIR%\stdout.log
echo     %LOG_DIR%\stderr.log
echo.
echo  ── Management commands (run in Admin CMD) ────────────
echo   Check status  : nssm.exe status StagingHub
echo   Stop server   : nssm.exe stop StagingHub
echo   Start server  : nssm.exe start StagingHub
echo   Restart       : nssm.exe restart StagingHub
echo   Edit settings : nssm.exe edit StagingHub
echo   Remove svc    : nssm.exe remove StagingHub confirm
echo  ──────────────────────────────────────────────────────
echo   After updating code → nssm.exe restart StagingHub
echo  ═══════════════════════════════════════════════════════
echo.
pause
