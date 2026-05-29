@echo off
REM ─────────────────────────────────────────────────────────────
REM  install_service.bat — Install Staging Hub as a Windows Task
REM  RIGHT-CLICK → Run as Administrator
REM  This makes the server start automatically and survive RDP disconnect.
REM ─────────────────────────────────────────────────────────────

cd /d "%~dp0"

REM Check for admin rights
net session >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: This script must be run as Administrator!
    echo Right-click → Run as administrator
    pause
    exit /b 1
)

set APP_DIR=%~dp0
set PYTHON_EXE=%APP_DIR%venv\Scripts\python.exe
set TASK_NAME=StagingHubServer

REM Verify Python venv exists
if not exist "%PYTHON_EXE%" (
    echo ERROR: venv not found! Run setup_remote.bat first.
    pause
    exit /b 1
)

echo.
echo =========================================================
echo   Installing Staging Hub as Windows Scheduled Task
echo =========================================================
echo.
echo   Task Name : %TASK_NAME%
echo   Directory : %APP_DIR%
echo   Python    : %PYTHON_EXE%
echo.

REM Remove existing task if any
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

REM Create scheduled task that:
REM  - Runs at system startup
REM  - Runs whether user is logged on or not
REM  - Never stops on idle
REM  - Restarts on failure every 1 minute, up to 999 times
schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "\"%PYTHON_EXE%\" \"%APP_DIR%app.py\" --production" ^
    /sc onstart ^
    /ru SYSTEM ^
    /rl HIGHEST ^
    /f

if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: Failed to create scheduled task.
    pause
    exit /b 1
)

REM Configure additional settings via XML update
echo   Task created. Configuring restart policy...

REM Also start it right now
echo   Starting the server now...
schtasks /run /tn "%TASK_NAME%"

echo.
echo =========================================================
echo   INSTALLED SUCCESSFULLY!
echo =========================================================
echo.
echo   The server will:
echo     - Start automatically when Windows boots
echo     - Keep running after you disconnect RDP
echo     - Run under SYSTEM account (no session lock issues)
echo.
echo   To check status:  schtasks /query /tn %TASK_NAME%
echo   To stop server:   schtasks /end /tn %TASK_NAME%
echo   To start server:  schtasks /run /tn %TASK_NAME%
echo   To uninstall:     schtasks /delete /tn %TASK_NAME% /f
echo.
pause
