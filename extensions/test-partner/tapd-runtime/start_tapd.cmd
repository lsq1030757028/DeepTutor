@echo off
REM mcp-server-tapd 8.0.80 - foreground start (streamable-http, 0.0.0.0:3795)
REM DeepTutor mount URL: http://host.docker.internal:3795/mcp
REM Port 3795 because 3790 is taken by the test-partner MCP server.
REM
REM NOTE: keep this file ASCII-only. cmd.exe reads .cmd in the OEM codepage,
REM so UTF-8 non-ASCII text here gets mangled and breaks batch parsing.
REM
REM Binding 0.0.0.0 is required for Docker containers to reach the host.
REM This server has NO authentication of any kind - see README.md before
REM running it on a machine that is not your own workstation.

setlocal
set "RUNTIME_DIR=%~dp0"
cd /d "%RUNTIME_DIR%" || exit /b 1

set "VENV_PY=%RUNTIME_DIR%.venv\Scripts\python.exe"
set "VENV_TAPD=%RUNTIME_DIR%.venv\Scripts\mcp-server-tapd.exe"
set "ENV_FILE=%RUNTIME_DIR%.env"

if not exist "%VENV_PY%" (
    echo [ERROR] .venv not found at "%VENV_PY%".
    echo         Rebuild it ^(needs Python 3.13, uv fetches a standalone one^):
    echo             py -3 -m pip install --user uv
    echo             py -3 -m uv venv tapd-runtime\.venv --python 3.13
    echo             tapd-runtime\.venv\Scripts\python.exe -m ensurepip --upgrade
    echo             tapd-runtime\.venv\Scripts\python.exe -m pip install mcp-server-tapd==8.0.80
    exit /b 1
)

if not exist "%ENV_FILE%" (
    echo [ERROR] .env not found at "%ENV_FILE%".
    echo         Copy .env.example to .env and fill in TAPD_ACCESS_TOKEN.
    exit /b 1
)

REM Drop anything inherited from the parent shell FIRST. Other tooling on this
REM box already exports TAPD_ACCESS_TOKEN, and silently inheriting it would let
REM the server come up on a credential nobody put in .env. .env is the only
REM source of truth here. TAPD_API_USER / TAPD_API_PASSWORD are cleared on
REM purpose: the Basic-Auth path is banned (see README, audit condition B).
set "TAPD_ACCESS_TOKEN="
set "TAPD_API_BASE_URL="
set "TAPD_BASE_URL="
set "TAPD_API_USER="
set "TAPD_API_PASSWORD="
set "BOT_URL="

REM Load .env line by line. eol=# skips comment lines; tokens=1,* keeps any
REM '=' inside the value intact. Values are never echoed.
REM .env MUST use CRLF line endings: for /f reads ZERO lines from a UTF-8
REM file that has LF-only endings, which would silently load nothing.
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
    if not "%%A"=="" if not "%%B"=="" set "%%A=%%B"
)

if not defined TAPD_ACCESS_TOKEN (
    echo [ERROR] TAPD_ACCESS_TOKEN is empty in "%ENV_FILE%".
    echo         Generate a personal access token in TAPD personal settings
    echo         and put it on the TAPD_ACCESS_TOKEN line. Do NOT use
    echo         TAPD_API_USER / TAPD_API_PASSWORD.
    echo         If the line IS filled in, check the file still has CRLF
    echo         line endings - cmd cannot read a UTF-8 .env saved with LF.
    exit /b 1
)

if not defined TAPD_API_BASE_URL (
    echo [ERROR] TAPD_API_BASE_URL is empty in "%ENV_FILE%".
    echo         It must stay pinned to https://api.tapd.cn - the package does
    echo         not validate it, so this line is the only gate on where the
    echo         token gets sent.
    exit /b 1
)

set "PYTHONIOENCODING=utf-8"

echo [tapd-runtime] mcp-server-tapd 8.0.80
echo [tapd-runtime] API base : %TAPD_API_BASE_URL%
echo [tapd-runtime] token    : loaded from .env ^(not shown^)
echo [tapd-runtime] endpoint : http://0.0.0.0:3795/mcp   ^(Ctrl+C to stop^)
echo [tapd-runtime] NOTE: the package calls TAPD /users/info at import time,
echo [tapd-runtime]       so a bad token or no network = startup crash.

REM Launched through the venv console script (which runs the venv Python).
REM Do NOT use "python -m mcp_server_tapd.server": the package's __init__
REM already imports the module, so runpy executes it a second time and the
REM import-time TAPD /users/info call fires twice.
"%VENV_TAPD%" --mode=streamable-http --host=0.0.0.0 --port=3795
