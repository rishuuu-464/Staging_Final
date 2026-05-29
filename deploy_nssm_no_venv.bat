@echo off
setlocal enabledelayedexpansion
REM ═══════════════════════════════════════════════════════════════════════
REM  deploy_nssm_no_venv.bat — Full Deployment with NSSM (No Venv)
REM
REM  CONSTRAINTS:
REM    ✓ Admin available (IT runs this as admin)
REM    ✗ No virtual environment — uses system Python directly
REM
REM  WHAT IT DOES:
REM    1. Finds system Python
REM    2. Installs all packages globally (pip install)
REM    3. Downloads NSSM if not present
REM    4. Installs "StagingHub" as a Windows Service
REM    5. Starts the service (survives reboot, RDP disconnect, crashes)
REM
REM  USAGE: RIGHT-CLICK → Run as administrator
REM ═══════════════════════════════════════════════════════════════════════

cd /d "%~dp0"

echo.
echo  ══════════════════════════════════════════════════════════════
echo     STAGING HUB — NSSM Deploy (No Venv)
echo  ══════════════════════════════════════════════════════════════
echo.

REM ── Admin check ─────────────────────────────────────────────────
net session >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Must be run as Administrator!
    echo          Right-click → Run as administrator
    pause
    exit /b 1
)
echo  [OK] Running as Administrator.
echo.

REM ── Configuration ───────────────────────────────────────────────
set APP_DIR=%~dp0
if "%APP_DIR:~-1%"=="\" set APP_DIR=%APP_DIR:~0,-1%

set SERVICE_NAME=StagingHub
set LOG_DIR=%APP_DIR%\logs
set NSSM_EXE=%APP_DIR%\nssm.exe
set HOST=0.0.0.0
set PORT=5000
set THREADS=8

REM ═══════════════════════════════════════════════════════════════════
REM  STEP 1: Find System Python
REM ═══════════════════════════════════════════════════════════════════
echo  [1/6] Finding Python...

set PYTHON_EXE=
for /f "delims=" %%i in ('where python 2^>nul') do (
    set PYTHON_EXE=%%i
    goto :found_python
)
for /f "delims=" %%i in ('where py 2^>nul') do (
    set PYTHON_EXE=%%i
    goto :found_python
)
echo  [ERROR] Python not found! Install Python 3.10+ with "Add to PATH".
pause
exit /b 1

:found_python
echo         %PYTHON_EXE%
"%PYTHON_EXE%" --version

REM Verify Python is installed for ALL users (not user-only)
echo.
echo         Verifying Python is accessible system-wide...
if exist "C:\Program Files\Python*" (
    echo         [OK] Python in Program Files — accessible to all accounts.
) else if exist "C:\Python*" (
    echo         [OK] Python in C:\Python — accessible to all accounts.
) else (
    echo.
    echo  [WARNING] Python may be installed for current user only!
    echo            NSSM runs as LOCAL SYSTEM which may NOT find it.
    echo.
    echo            FIX: Reinstall Python with "Install for all users" checked.
    echo            OR:  After install, we can set the service to run as your user.
    echo.
    set /p CONTINUE="         Continue anyway? (Y/N): "
    if /i not "!CONTINUE!"=="Y" (
        pause
        exit /b 1
    )
)
echo.

REM ═══════════════════════════════════════════════════════════════════
REM  STEP 2: Install Packages (global — no venv)
REM ═══════════════════════════════════════════════════════════════════
echo  [2/6] Installing Python packages (system-wide)...

"%PYTHON_EXE%" -m pip install --upgrade pip 2>nul

echo         Core packages...
"%PYTHON_EXE%" -m pip install flask gspread python-dotenv pytz requests apscheduler waitress
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Package install failed!
    pause
    exit /b 1
)

echo         Google auth packages...
"%PYTHON_EXE%" -m pip install google-auth google-auth-oauthlib google-api-python-client

echo         Playwright...
set PLAYWRIGHT_BROWSERS_PATH=%APP_DIR%\.browsers
"%PYTHON_EXE%" -m pip install playwright >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set PLAYWRIGHT_BROWSERS_PATH=%APP_DIR%\.browsers
    "%PYTHON_EXE%" -m playwright install chromium >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo         Playwright browser ready.
        echo         Browser stored in: %APP_DIR%\.browsers
    ) else (
        echo         WARNING: Playwright browser failed — HMS sync may not work.
    )
) else (
    echo         WARNING: Playwright failed — HMS sync may not work.
)
echo         Done.
echo.

REM ═══════════════════════════════════════════════════════════════════
REM  STEP 3: Verify Required Files
REM ═══════════════════════════════════════════════════════════════════
echo  [3/6] Verifying files...

if not exist "%APP_DIR%\credentials.json" (
    echo         [MISSING] credentials.json
) else (
    echo         credentials.json .... OK
)
if not exist "%APP_DIR%\app.py" (
    echo         [MISSING] app.py — CRITICAL
) else (
    echo         app.py .............. OK
)
if not exist "%APP_DIR%\sheets.py" (
    echo         [MISSING] sheets.py
) else (
    echo         sheets.py ........... OK
)
if exist "%APP_DIR%\token.json" (
    echo         token.json .......... OK
) else (
    echo         [INFO] token.json missing — first run needs Google auth.
)
echo.

REM ═══════════════════════════════════════════════════════════════════
REM  STEP 4: Get NSSM
REM ═══════════════════════════════════════════════════════════════════
echo  [4/6] Checking NSSM...

if exist "%NSSM_EXE%" (
    echo         nssm.exe found.
    goto :nssm_ready
)

echo         Downloading nssm.exe...
powershell -Command "& { $ProgressPreference='SilentlyContinue'; try { Invoke-WebRequest -Uri 'https://nssm.cc/release/nssm-2.24.zip' -OutFile '%APP_DIR%\nssm.zip' -UseBasicParsing; Write-Host 'OK' } catch { Write-Host 'FAILED'; exit 1 } }"
if %ERRORLEVEL% neq 0 (
    echo.
    echo  [ERROR] Download failed. Manually download from https://nssm.cc/download
    echo          Extract win64\nssm.exe into: %APP_DIR%\
    pause
    exit /b 1
)

echo         Extracting...
powershell -Command "& { Add-Type -A 'System.IO.Compression.FileSystem'; $zip = [IO.Compression.ZipFile]::OpenRead('%APP_DIR%\nssm.zip'); $entry = $zip.Entries | Where-Object { $_.FullName -like '*/win64/nssm.exe' } | Select-Object -First 1; if ($entry) { [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, '%NSSM_EXE%', $true) } else { exit 1 }; $zip.Dispose() }"
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Extract failed. Put nssm.exe manually in: %APP_DIR%\
    pause
    exit /b 1
)
del "%APP_DIR%\nssm.zip" >nul 2>&1
echo         nssm.exe ready.

:nssm_ready
echo.

REM ═══════════════════════════════════════════════════════════════════
REM  STEP 5: Create Directories
REM ═══════════════════════════════════════════════════════════════════
echo  [5/6] Creating directories...
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if not exist "%APP_DIR%\_cache" mkdir "%APP_DIR%\_cache"
echo         logs\ and _cache\ ready.
echo.

REM ═══════════════════════════════════════════════════════════════════
REM  STEP 6: Install NSSM Service
REM ═══════════════════════════════════════════════════════════════════
echo  [6/6] Installing Windows Service...

REM Remove old service
"%NSSM_EXE%" stop %SERVICE_NAME% >nul 2>&1
timeout /t 2 /nobreak >nul
"%NSSM_EXE%" remove %SERVICE_NAME% confirm >nul 2>&1
echo         Old service cleared.

REM Install
"%NSSM_EXE%" install %SERVICE_NAME% "%PYTHON_EXE%"

REM Configure — run waitress directly via system python
"%NSSM_EXE%" set %SERVICE_NAME% AppParameters "-m waitress --host=%HOST% --port=%PORT% --threads=%THREADS% app:app"
"%NSSM_EXE%" set %SERVICE_NAME% AppDirectory "%APP_DIR%"

REM Environment (include PLAYWRIGHT_BROWSERS_PATH so service finds chromium)
"%NSSM_EXE%" set %SERVICE_NAME% AppEnvironmentExtra "STAGING_PRODUCTION=true" "PYTHONIOENCODING=utf-8" "PYTHONUTF8=1" "PLAYWRIGHT_BROWSERS_PATH=%APP_DIR%\.browsers"

REM Logging
"%NSSM_EXE%" set %SERVICE_NAME% AppStdout "%LOG_DIR%\stdout.log"
"%NSSM_EXE%" set %SERVICE_NAME% AppStderr "%LOG_DIR%\stderr.log"
"%NSSM_EXE%" set %SERVICE_NAME% AppStdoutCreationDisposition 4
"%NSSM_EXE%" set %SERVICE_NAME% AppStderrCreationDisposition 4
"%NSSM_EXE%" set %SERVICE_NAME% AppRotateFiles 1
"%NSSM_EXE%" set %SERVICE_NAME% AppRotateBytes 10485760

REM Auto-restart on crash
"%NSSM_EXE%" set %SERVICE_NAME% AppExit Default Restart
"%NSSM_EXE%" set %SERVICE_NAME% AppRestartDelay 5000

REM Display
"%NSSM_EXE%" set %SERVICE_NAME% DisplayName "Staging Hub — LNTZ Worker Portal"
"%NSSM_EXE%" set %SERVICE_NAME% Description "Production WSGI server for LNTZ Staging warehouse scanning system."

REM Auto-start on boot
"%NSSM_EXE%" set %SERVICE_NAME% Start SERVICE_AUTO_START

REM Run as LOCAL SYSTEM (default) — ensures it works without any user login.
REM If you face permission issues, uncomment below to run as a specific user:
REM "%NSSM_EXE%" set %SERVICE_NAME% ObjectName ".\YourUsername" "YourPassword"

echo         Service configured.

REM Start it
echo         Starting...
"%NSSM_EXE%" start %SERVICE_NAME%
timeout /t 5 /nobreak >nul

REM Verify it actually started
"%NSSM_EXE%" status %SERVICE_NAME% | findstr /i "running" >nul
if %ERRORLEVEL% equ 0 (
    echo.
    echo  [OK] Service is RUNNING!
) else (
    echo.
    echo  [WARNING] Service may not be running. Check status:
    "%NSSM_EXE%" status %SERVICE_NAME%
    echo.
    echo  Common fixes:
    echo    1. Check logs: %LOG_DIR%\stderr.log
    echo    2. If "python not found" — reinstall Python for all users
    echo    3. Run: nssm edit StagingHub  (to set a user account)
    echo.
)

echo.
echo  ══════════════════════════════════════════════════════════════
echo     DONE! Service is running.
echo  ══════════════════════════════════════════════════════════════
echo.
echo   Portal    : http://localhost:%PORT%
echo   Dashboard : http://localhost:%PORT%/dashboard
echo.
echo   Logs      : %LOG_DIR%\stdout.log
echo               %LOG_DIR%\stderr.log
echo.
echo  ── Commands (Admin CMD) ──────────────────────────────────────
echo   nssm status StagingHub
echo   nssm restart StagingHub
echo   nssm stop StagingHub
echo   nssm start StagingHub
echo   nssm remove StagingHub confirm
echo  ──────────────────────────────────────────────────────────────
echo.
pause
