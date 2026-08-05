@echo off
REM ============================================================================
REM  install_autostart.cmd - register the test-partner capability gateway to
REM  start automatically when YOU log on to Windows.
REM
REM  This script only CREATES a scheduled task. It does not start the gateway,
REM  it does not touch any credential, and it does not change any system or
REM  security setting. Run uninstall_autostart.cmd to remove it again.
REM
REM  KEEP THIS FILE ASCII-ONLY. cmd.exe parses .cmd in the OEM codepage, so
REM  non-ASCII bytes here get mangled and break batch parsing.
REM ============================================================================

setlocal EnableExtensions
title test-partner autostart

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_DIR=%%~fI"
set "TASK_NAME=test-partner-gateway"
set "START_CMD=%REPO_DIR%\scripts\start_server.cmd"
set "VENV_PY=%REPO_DIR%\.venv\Scripts\python.exe"

echo.
echo   test-partner capability gateway - autostart setup
echo   ------------------------------------------------
echo.
echo   About to create a Windows Scheduled Task:
echo.
echo       Task name : %TASK_NAME%
echo       Trigger   : ONLOGON  (only for the account running this script)
echo       Action    : "%START_CMD%"
echo       Run level : normal user (NOT elevated)
echo.
echo   What that means in practice:
echo     - Every time you log in, one process starts. It serves the MCP tools
echo       on port 3790 and the local config page on 127.0.0.1:3789.
echo     - Nothing is installed as a service. Nothing runs as SYSTEM.
echo     - No credential is read, written or copied by this script.
echo     - Remove it any time with scripts\uninstall_autostart.cmd, or from
echo       Task Scheduler (taskschd.msc) by deleting "%TASK_NAME%".
echo.

if not exist "%START_CMD%" (
    echo   [ERROR] Cannot find "%START_CMD%".
    echo           Run this script from inside the test-partner repo.
    goto fail
)
if not exist "%VENV_PY%" (
    echo   [WARN]  .venv not found at "%VENV_PY%".
    echo           The task will be created, but the gateway will fail to start
    echo           until you build the venv:
    echo               py -3 -m venv .venv
    echo               .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
)

schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if not errorlevel 1 (
    echo   NOTE: a task named "%TASK_NAME%" already exists.
    echo         Continuing will overwrite it.
    echo.
)

echo   Press Ctrl+C now to abort, or
pause

schtasks /create /tn "%TASK_NAME%" /tr "\"%START_CMD%\"" /sc onlogon /rl limited /f
if errorlevel 1 (
    echo.
    echo   [ERROR] schtasks refused to create the task (see the message above).
    echo           Most common cause: this account is not allowed to create
    echo           scheduled tasks. Ask your IT admin, or just start the gateway
    echo           by hand with scripts\start_server.cmd when you need it.
    goto fail
)

echo.
echo   Done. The gateway will start at your next logon.
echo   To start it right now without logging out:
echo       schtasks /run /tn "%TASK_NAME%"
echo   Config page once it is up: http://localhost:3789
echo.
pause
exit /b 0

:fail
echo.
pause
exit /b 1
