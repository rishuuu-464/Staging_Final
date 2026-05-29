@echo off
setlocal enabledelayedexpansion
REM ─────────────────────────────────────────────────────────────
REM  setup_remote.bat — One-time setup for Staging Hub
REM  Run this ONCE on the remote desktop after copying the folder.
REM  Host: ACXW-FSCDAEXT  |  IP: 172.31.44.121
REM ─────────────────────────────────────────────────────────────

cd /d "%~dp0"
echo.
echo =========================================================
echo   STAGING HUB — Remote Desktop Setup
echo =========================================================
echo.

REM ── Step 1: Check Python ──
echo [1/4] Checking Python...
set PYTHON_CMD=
py --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set PYTHON_CMD=py
    goto :python_found
)
python --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set PYTHON_CMD=python
    goto :python_found
)
echo   ERROR: Python is not installed or not in PATH.
echo   Install Python 3.10+ from https://www.python.org/downloads/
echo   Make sure to check "Add Python to PATH" during install.
pause
exit /b 1
:python_found
%PYTHON_CMD% --version
echo.

REM ── Step 2: Create virtual environment ──
echo [2/4] Creating virtual environment...
if not exist venv\Scripts\python.exe goto :venv_create
venv\Scripts\python.exe --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo   venv already exists — skipping creation.
    goto :venv_ready
)
echo   venv is broken (old Python path). Recreating...
rmdir /s /q venv
:venv_create
%PYTHON_CMD% -m venv venv
if %ERRORLEVEL% neq 0 (
    echo   ERROR: Failed to create venv.
    pause
    exit /b 1
)
echo   venv created.
:venv_ready
echo.

REM ── Step 3: Install dependencies ──
echo [3/4] Installing Python packages...
call venv\Scripts\activate.bat
venv\Scripts\python.exe -m pip install --upgrade pip >nul 2>&1

REM Install smaller packages first (these survive even if network drops later)
echo   Installing core packages...
venv\Scripts\python.exe -m pip install flask gspread python-dotenv pytz requests apscheduler
if %ERRORLEVEL% neq 0 (
    echo   Retrying core packages...
    venv\Scripts\python.exe -m pip install flask gspread python-dotenv pytz requests apscheduler
)

echo   Installing Google packages...
venv\Scripts\python.exe -m pip install google-auth google-auth-oauthlib google-api-python-client
if %ERRORLEVEL% neq 0 (
    echo   Retrying Google packages...
    venv\Scripts\python.exe -m pip install google-auth google-auth-oauthlib google-api-python-client
)

REM Playwright is large (~37MB) — retry up to 3 times
echo   Installing Playwright (large download, may take a while)...
set PW_OK=0
for /L %%i in (1,1,3) do (
    if !PW_OK! equ 0 (
        venv\Scripts\python.exe -m pip install playwright
        if !ERRORLEVEL! equ 0 set PW_OK=1
        if !PW_OK! equ 0 echo   Attempt %%i failed, retrying...
    )
)
if %PW_OK% equ 0 (
    echo   ERROR: Playwright install failed after 3 attempts.
    echo   Check your network connection and try again.
    pause
    exit /b 1
)
echo   All packages installed.
echo.

REM ── Step 4: Install Playwright Chrome ──
echo [4/4] Installing Playwright Chrome browser...
venv\Scripts\python.exe -m playwright install chrome
if %ERRORLEVEL% neq 0 (
    echo   WARNING: Playwright Chrome install failed.
    echo   If Chrome is already installed on this PC, that's OK.
)
echo.

REM ── Verify required files ──
echo ─────────────────────────────────────────────────────────
echo   Checking required files...
if not exist credentials.json (
    echo   WARNING: credentials.json NOT FOUND — Google auth will fail!
) else (
    echo   credentials.json .... OK
)
if not exist .env (
    echo   WARNING: .env NOT FOUND — Spreadsheet ID missing!
) else (
    echo   .env ................ OK
)
if exist token.json (
    echo   token.json .......... OK
) else (
    echo   NOTE: token.json not found — will prompt for Google auth on first run.
)
echo ─────────────────────────────────────────────────────────
echo.
echo =========================================================
echo   SETUP COMPLETE!
echo   Now double-click start_server.bat to launch.
echo =========================================================
echo.
pause
