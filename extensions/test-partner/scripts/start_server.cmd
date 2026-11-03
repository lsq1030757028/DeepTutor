@echo off
REM test-partner capability gateway - foreground start. One process, two faces:
REM   MCP face    : streamable-http on 0.0.0.0:3790
REM                 DeepTutor mount URL: http://host.docker.internal:3790/mcp
REM   Config face : local config page on 127.0.0.1:3789 (paste tokens there)
REM This is still the ONLY entry point. tapd-runtime\setup_tapd.cmd is retired
REM to a troubleshooting tool - the config page replaces it.
REM NOTE: keep this file ASCII-only. cmd.exe reads .cmd in the OEM codepage,
REM so UTF-8 non-ASCII text here gets mangled and breaks batch parsing.

setlocal
set "REPO_ROOT=%~dp0.."
cd /d "%REPO_ROOT%" || exit /b 1

set "VENV_PY=%REPO_ROOT%\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo [ERROR] .venv not found. Run these first:
    echo     py -3 -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    exit /b 1
)

call "%REPO_ROOT%\.venv\Scripts\activate.bat"

set "PYTHONIOENCODING=utf-8"

if not defined DEEPTUTOR_ENV_FILE set "DEEPTUTOR_ENV_FILE=%REPO_ROOT%\..\..\.env"
"%VENV_PY%" -m server.journey.bridge_runtime --deeptutor-env "%DEEPTUTOR_ENV_FILE%"
if errorlevel 1 (
    echo [ERROR] Journey bridge preflight failed. No listener was started.
    exit /b 1
)

echo [test-partner] starting capability gateway  (Ctrl+C to stop)
echo [test-partner]   config page : http://localhost:3789    ^<- open this to set tokens
echo [test-partner]   MCP endpoint: http://0.0.0.0:3790/mcp
"%VENV_PY%" -m server.main
