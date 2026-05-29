@echo off
setlocal enabledelayedexpansion
REM ═══════════════════════════════════════════════════════════════
REM  deploy_vm.bat — One-click VM Deployment for Staging Hub
REM  
REM  WHAT THIS DOES:
REM    1. Checks Python is installed
REM    2. Creates venv + installs all packages
REM    3. Installs Playwright Chrome
REM    4. Verifies required files (credentials.json, .env)
REM    5. Launches the server in IMMORTAL mode (auto-restarts on crash)
REM
REM  USAGE: Double-click this file on the VM.
REM         The server will NEVER stop — it auto-restarts on any crash.
REM         Close this window manually (or Ctrl+C) to kill the server.
REM
REM  TOKEN.JSON: You do NOT need to delete it.
REM         Copy it along with the project. It will auto-refresh.
REM         If auth fails, delete token.json and re-run — it will
REM         open a browser for Google login (one-time only).
REM ═══════════════════════════════════════════════════════════════

cd /d "%~dp0"

echo.
echo  ══════════════════════════════════════════════════════════
echo    STAGING HUB — VM Deployment Script
echo  ══════════════════════════════════════════════════════════
echo.

REM ── Step 1: Find Python ─────────────────────────────────────
echo [1/5] Checking Python installation...
set PYTHON_CMD=
where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set PYTHON_CMD=py
    goto :python_ok
)
where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set PYTHON_CMD=python
    goto :python_ok
)
echo.
echo  ERROR: Python not found! Install Python 3.10+ first.
echo  Download: https://www.python.org/downloads/
echo  IMPORTANT: Check "Add Python to PATH" during install.
echo.
pause
exit /b 1

:python_ok
echo   Found: & %PYTHON_CMD% --version
echo.

REM ── Step 2: Create/verify venv ──────────────────────────────
echo [2/5] Setting up virtual environment...
if exist venv\Scripts\python.exe (
    venv\Scripts\python.exe --version >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo   venv exists and is healthy — skipping creation.
        goto :venv_done
    )
    echo   venv is broken. Recreating...
    rmdir /s /q venv
)
%PYTHON_CMD% -m venv venv
if %ERRORLEVEL% neq 0 (
    echo   ERROR: Failed to create venv.
    pause
    exit /b 1
)
echo   venv created successfully.
:venv_done
echo.

REM ── Step 3: Install packages ────────────────────────────────
echo [3/5] Installing Python packages...
call venv\Scripts\activate.bat
venv\Scripts\python.exe -m pip install --upgrade pip >nul 2>&1

echo   Installing core packages...
venv\Scripts\python.exe -m pip install flask gspread python-dotenv pytz requests apscheduler waitress
if %ERRORLEVEL% neq 0 (
    echo   Retrying core packages...
    venv\Scripts\python.exe -m pip install flask gspread python-dotenv pytz requests apscheduler waitress
)

echo   Installing Google auth packages...
venv\Scripts\python.exe -m pip install google-auth google-auth-oauthlib google-api-python-client
if %ERRORLEVEL% neq 0 (
    echo   Retrying Google auth packages...
    venv\Scripts\python.exe -m pip install google-auth google-auth-oauthlib google-api-python-client
)

echo   Installing Playwright...
set PW_OK=0
for /L %%i in (1,1,3) do (
    if !PW_OK! equ 0 (
        venv\Scripts\python.exe -m pip install playwright >nul 2>&1
        if !ERRORLEVEL! equ 0 set PW_OK=1
    )
)
if %PW_OK% equ 0 (
    echo   WARNING: Playwright install failed. HMS sync may not work.
)
echo   All packages installed.
echo.

REM ── Step 4: Install Playwright browser ──────────────────────
echo [4/5] Installing Playwright Chrome browser...
venv\Scripts\python.exe -m playwright install chromium >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo   WARNING: Playwright browser install failed — HMS sync may not work.
) else (
    echo   Playwright browser ready.
)
echo.

REM ── Step 5: Verify required files ──────────────────────────
echo [5/5] Verifying required files...
set MISSING=0

if not exist credentials.json (
    echo   MISSING: credentials.json — Google auth will FAIL!
    set MISSING=1
) else (
    echo   credentials.json .... OK
)

if not exist .env (
    echo   MISSING: .env — Spreadsheet IDs will be missing!
    set MISSING=1
) else (
    echo   .env ................ OK
)

if exist token.json (
    echo   token.json .......... OK (will auto-refresh)
) else (
    echo   token.json .......... NOT FOUND (will prompt for Google login on first run)
)

if not exist _cache (
    mkdir _cache
    echo   _cache folder ....... CREATED
) else (
    echo   _cache folder ....... OK
)

if not exist logs (
    mkdir logs
    echo   logs folder ......... CREATED
) else (
    echo   logs folder ......... OK
)

if %MISSING% equ 1 (
    echo.
    echo  WARNING: Missing required files! Server may not work properly.
    echo  Copy credentials.json and .env into this folder.
    echo.
    pause
)

echo.
echo  ══════════════════════════════════════════════════════════
echo    SETUP COMPLETE — Starting server in IMMORTAL mode
echo    The server will auto-restart if it crashes.
echo    Close this window to stop the server.
echo  ══════════════════════════════════════════════════════════
echo.

REM ══════════════════════════════════════════════════════════════
REM  IMMORTAL LOOP — Server never dies
REM  If the Python process exits (crash, unhandled exception, OOM),
REM  it waits 5 seconds and restarts automatically.
REM ══════════════════════════════════════════════════════════════

:restart_loop
echo.
echo  [%date% %time%] Starting Staging Hub server...
echo  ──────────────────────────────────────────────────────────

venv\Scripts\python.exe app.py --production

echo.
echo  ══════════════════════════════════════════════════════════
echo  SERVER CRASHED or STOPPED at %date% %time%
echo  Auto-restarting in 5 seconds...
echo  (Press Ctrl+C NOW to stop permanently)
echo  ══════════════════════════════════════════════════════════

REM Log the crash
echo [%date% %time%] Server exited — auto-restarting >> logs\crash_restart.log

timeout /t 5 /nobreak >nul
goto :restart_loop
