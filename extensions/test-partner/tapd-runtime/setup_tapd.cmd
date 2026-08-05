@echo off
REM ============================================================================
REM  SUPERSEDED BY THE CONFIG PAGE - troubleshooting tool only.
REM
REM  Normal setup is now: start the gateway (scripts\start_server.cmd), open
REM  http://localhost:3789 in a browser, paste the token, click save. The
REM  gateway writes config\secrets.env, starts mcp-server-tapd as its own child
REM  process and registers it into DeepTutor - same four steps as below, but
REM  with a masked input box and a status light instead of a console window.
REM
REM  This script is kept because it does not depend on the gateway: when the
REM  gateway itself will not start, this is how you prove the TAPD side works.
REM  It writes tapd-runtime\.env, which the gateway does NOT read - the two
REM  paths have separate credential files on purpose. If you use both, the
REM  gateway's injected token wins inside the child process (python-dotenv
REM  does not override variables that already exist in the environment).
REM
REM  setup_tapd.cmd - one-shot TAPD setup. Double-click it, paste your token.
REM
REM  Replaces the old three manual steps (edit .env / run start_tapd.cmd /
REM  fill the DeepTutor deployment registry by hand). Idempotent: re-running it
REM  with a filled-in .env and a live server just re-registers and exits.
REM
REM  KEEP THIS FILE ASCII-ONLY. cmd.exe parses .cmd in the OEM codepage, so
REM  non-ASCII bytes here get mangled and break batch parsing. All Chinese
REM  prose lives in register_tapd.py (UTF-8) and is printed through Python -
REM  that is what the "--explain" calls below are for.
REM ============================================================================

setlocal EnableExtensions
title TAPD setup

REM UTF-8 console + UTF-8 Python stdout, so register_tapd.py's Chinese output
REM renders on any Windows locale instead of raising UnicodeEncodeError.
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"

set "RUNTIME_DIR=%~dp0"
cd /d "%RUNTIME_DIR%"
if errorlevel 1 (
    echo [ERROR] Cannot enter "%RUNTIME_DIR%".
    goto end_fail_bare
)

REM ---------------------------------------------------------------- [0/4] py
for %%I in ("%RUNTIME_DIR%..") do set "REPO_DIR=%%~fI"
set "MAIN_PY=%REPO_DIR%\.venv\Scripts\python.exe"
if not exist "%MAIN_PY%" set "MAIN_PY=%RUNTIME_DIR%.venv\Scripts\python.exe"
if not exist "%MAIN_PY%" (
    echo [ERROR] No usable Python found.
    echo         Expected "%REPO_DIR%\.venv\Scripts\python.exe".
    echo         Build the test-partner venv first, then re-run this script.
    goto end_fail_bare
)

echo.
echo   TAPD setup  -  one script, one token, nothing else to do.
echo.
echo   NOTE: this script has been superseded by the gateway config page at
echo         http://localhost:3789  (start it with scripts\start_server.cmd).
echo         Use this script only when the gateway itself will not start.
echo.

REM ------------------------------------------------------------- [1/4] token
REM Drop anything inherited from the parent shell BEFORE reading .env: this box
REM already exports TAPD_ACCESS_TOKEN for other tooling, and inheriting it would
REM make the script think the token is configured when .env is actually empty.
set "TAPD_ACCESS_TOKEN="
set "TAPD_API_BASE_URL="
set "TAPD_BASE_URL="
set "TAPD_API_USER="
set "TAPD_API_PASSWORD="
set "BOT_URL="

if exist ".env" (
    REM eol=# skips comments; tokens=1,* keeps any '=' inside the value intact.
    REM Values are never echoed. .env must be CRLF - cmd reads ZERO lines from a
    REM LF-only file, which would look like "the token I typed disappeared".
    for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
        if not "%%A"=="" if not "%%B"=="" set "%%A=%%B"
    )
)

if defined TAPD_ACCESS_TOKEN (
    echo [1/4] .env already has a token - skipping entry.
    goto start_service
)

echo [1/4] No token yet.
echo.
"%MAIN_PY%" register_tapd.py --explain token-prompt
echo.
set "TOKEN_INPUT="
set /p "TOKEN_INPUT=TAPD token: "
if not defined TOKEN_INPUT (
    echo.
    echo [ERROR] Nothing was entered. Re-run this script and paste the token.
    goto end_fail
)
call :write_env
echo [1/4] Token written to .env  ^(gitignored; the value is not printed anywhere^).

REM ----------------------------------------------------------- [2-3/4] serve
:start_service
echo.
echo [2/4] Checking port 3795 ...
call :probe_port
if not errorlevel 1 (
    echo [2/4] Port 3795 is already serving - reusing the running instance.
    goto started
)

echo [3/4] Starting mcp-server-tapd ...
if exist "tapd-start.log" del /q "tapd-start.log" >nul 2>&1
REM A separate minimized console (NOT "start /b"): a /b child shares this
REM console, so closing the setup window when it says "done" would take the
REM server down with it. The spawned window IS the stop button - close it to
REM stop TAPD. Output goes to tapd-start.log so a crash is readable.
REM ".\" is required, not cosmetic: when NoDefaultCurrentDirectoryInExePath=1
REM is set (it is, in some managed/CI shells), a bare "start_tapd.cmd" is not
REM resolved from the current directory and the child dies with
REM "'start_tapd.cmd' is not recognized".
start "tapd-runtime :3795  (close this window to stop TAPD)" /min "%ComSpec%" /c ".\start_tapd.cmd > tapd-start.log 2>&1"

REM The package calls TAPD /users/info at import time, so startup includes one
REM real network round-trip. 3s, then poll for up to 8 more seconds.
call :sleep 3
set "TRIES=0"
:probe_loop
call :probe_port
if not errorlevel 1 goto started
set /a TRIES+=1
if %TRIES% GEQ 8 goto start_failed
call :sleep 1
goto probe_loop

:start_failed
echo.
echo [ERROR] Port 3795 never came up.
echo.
REM Deliberately NOT dumping tapd-start.log here: the package's import-time
REM crash produces a 100-line traceback that buries the one line that matters.
REM --explain start-failed reads the log, classifies it, and prints the fix.
"%MAIN_PY%" register_tapd.py --explain start-failed
goto end_fail

:started
echo [3/4] Server is up on 127.0.0.1:3795.

REM ---------------------------------------------------------- [4/4] register
echo.
echo [4/4] Registering into DeepTutor ...
"%MAIN_PY%" register_tapd.py
if errorlevel 1 goto end_fail

echo.
echo   Done. The minimized "tapd-runtime :3795" window keeps the service alive;
echo   close it when you want to stop TAPD. Re-run this script any time.
echo.
pause
exit /b 0

REM ------------------------------------------------------------------ helpers
:probe_port
REM Sets errorlevel 0 when something is listening on 127.0.0.1:3795.
"%MAIN_PY%" -c "import socket;socket.create_connection(('127.0.0.1',3795),2).close()" >nul 2>&1
goto :eof

:sleep
REM Sleep %1 seconds. "timeout" refuses to run when stdin is redirected
REM (piped/CI runs), so fall back to ping, which does not care.
timeout /t %1 /nobreak >nul 2>&1
if not errorlevel 1 goto :eof
set /a _SLEEP_N=%1+1
ping -n %_SLEEP_N% 127.0.0.1 >nul 2>&1
goto :eof

:write_env
REM Delayed expansion so a token containing & | ^ is written verbatim instead of
REM being parsed as shell syntax. echo emits CRLF, which start_tapd.cmd requires.
setlocal EnableDelayedExpansion
:trim_head
if not defined TOKEN_INPUT goto trim_done
if "!TOKEN_INPUT:~0,1!"==" " (
    set "TOKEN_INPUT=!TOKEN_INPUT:~1!"
    goto trim_head
)
:trim_tail
if not defined TOKEN_INPUT goto trim_done
if "!TOKEN_INPUT:~-1!"==" " (
    set "TOKEN_INPUT=!TOKEN_INPUT:~0,-1!"
    goto trim_tail
)
:trim_done
> ".env" echo # Written by setup_tapd.cmd. Real credential - never commit.
>> ".env" echo # .gitignore already blocks this file. Delete it and re-run the
>> ".env" echo # script to switch tokens. Keep CRLF line endings.
>> ".env" echo TAPD_ACCESS_TOKEN=!TOKEN_INPUT!
>> ".env" echo # Pinned on purpose: mcp-server-tapd does not validate base_url,
>> ".env" echo # so this line is the only gate on where the token gets sent.
>> ".env" echo TAPD_API_BASE_URL=https://api.tapd.cn
endlocal
goto :eof

:end_fail
echo.
pause
exit /b 1

:end_fail_bare
echo.
pause
exit /b 1
