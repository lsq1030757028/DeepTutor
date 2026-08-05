@echo off
REM ============================================================================
REM  uninstall_autostart.cmd - remove the scheduled task created by
REM  install_autostart.cmd. Does not stop a gateway that is already running
REM  (close its window for that) and does not delete any file or credential.
REM
REM  KEEP THIS FILE ASCII-ONLY.
REM ============================================================================

setlocal EnableExtensions
title test-partner autostart removal

set "TASK_NAME=test-partner-gateway"

echo.
echo   test-partner capability gateway - autostart removal
echo   ---------------------------------------------------
echo.
echo   About to delete the Windows Scheduled Task "%TASK_NAME%".
echo   Your config files, credentials and the repo itself are NOT touched.
echo   A gateway that is currently running keeps running until you close it.
echo.

schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if errorlevel 1 (
    echo   Nothing to do: no task named "%TASK_NAME%" exists.
    echo.
    pause
    exit /b 0
)

echo   Press Ctrl+C now to abort, or
pause

schtasks /delete /tn "%TASK_NAME%" /f
if errorlevel 1 (
    echo.
    echo   [ERROR] schtasks refused to delete the task (see above).
    echo           You can also remove it from Task Scheduler (taskschd.msc).
    echo.
    pause
    exit /b 1
)

echo.
echo   Done. The gateway will no longer start automatically at logon.
echo.
pause
exit /b 0
