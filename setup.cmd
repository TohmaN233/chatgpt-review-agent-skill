@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "DEFAULT_REPO=%CD%"
if defined CODEX_HOME (
  set "DEFAULT_SKILLS=%CODEX_HOME%\skills"
) else (
  set "DEFAULT_SKILLS=%USERPROFILE%\.codex\skills"
)

if defined REVIEW_REPO_ROOT (set "REPO_ROOT=%REVIEW_REPO_ROOT%") else (set "REPO_ROOT=%DEFAULT_REPO%")
if defined REVIEW_SKILLS_ROOT (set "SKILLS_ROOT=%REVIEW_SKILLS_ROOT%") else (set "SKILLS_ROOT=%DEFAULT_SKILLS%")
if defined REVIEW_PUBLIC_URL (set "PUBLIC_URL=%REVIEW_PUBLIC_URL%") else (set "PUBLIC_URL=")
if defined REVIEW_HOST (set "HOST=%REVIEW_HOST%") else (set "HOST=127.0.0.1")
if defined REVIEW_PORT (set "PORT=%REVIEW_PORT%") else (set "PORT=8765")
if defined REVIEW_ENABLE_EDIT (set "EDIT=%REVIEW_ENABLE_EDIT%") else (set "EDIT=n")
if defined REVIEW_TOKEN_FILE (set "TOKEN_FILE=%REVIEW_TOKEN_FILE%") else (set "TOKEN_FILE=%SCRIPT_DIR%.review-mcp-token")

set "EDIT_ARG="
if /I "%EDIT%"=="y" set "EDIT_ARG=--enable-edit"
if /I "%EDIT%"=="yes" set "EDIT_ARG=--enable-edit"

> "%SCRIPT_DIR%start-review-mcp.cmd" echo @echo off
if defined PUBLIC_URL (
  >> "%SCRIPT_DIR%start-review-mcp.cmd" echo python "%SCRIPT_DIR%mcp_server.py" --root "%REPO_ROOT%" --root "%SKILLS_ROOT%" --host "%HOST%" --port "%PORT%" --public-url "%PUBLIC_URL%" --token-file "%TOKEN_FILE%" %EDIT_ARG%
) else (
  >> "%SCRIPT_DIR%start-review-mcp.cmd" echo python "%SCRIPT_DIR%mcp_server.py" --root "%REPO_ROOT%" --root "%SKILLS_ROOT%" --host "%HOST%" --port "%PORT%" --token-file "%TOKEN_FILE%" %EDIT_ARG%
)
>> "%SCRIPT_DIR%start-review-mcp.cmd" echo pause

echo Generated:
echo   %SCRIPT_DIR%start-review-mcp.cmd
echo Token file:
echo   %TOKEN_FILE%
if defined PUBLIC_URL (
  echo Connector endpoint: %PUBLIC_URL%/mcp
) else (
  echo Connector endpoint: https://your-public-host/mcp
)

endlocal
