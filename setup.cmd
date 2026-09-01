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
if defined REVIEW_NGROK_STATIC_DOMAIN (set "NGROK_STATIC_DOMAIN=%REVIEW_NGROK_STATIC_DOMAIN%") else (set "NGROK_STATIC_DOMAIN=")
if defined REVIEW_OPENAI_TUNNEL_ID (set "OPENAI_TUNNEL_ID=%REVIEW_OPENAI_TUNNEL_ID%") else (set "OPENAI_TUNNEL_ID=")

REM When only ngrok static domain is set, use it as the MCP public URL (no /mcp suffix).
if "%PUBLIC_URL%"=="" if not "%NGROK_STATIC_DOMAIN%"=="" set "PUBLIC_URL=https://%NGROK_STATIC_DOMAIN%"

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

REM Generate ngrok launcher if static domain is provided
if defined NGROK_STATIC_DOMAIN (
  > "%SCRIPT_DIR%start-ngrok-tunnel.cmd" echo @echo off
  >> "%SCRIPT_DIR%start-ngrok-tunnel.cmd" echo REM Start ngrok with your static domain
  >> "%SCRIPT_DIR%start-ngrok-tunnel.cmd" echo REM Make sure ngrok is installed and authenticated
  >> "%SCRIPT_DIR%start-ngrok-tunnel.cmd" echo ngrok http --url=%NGROK_STATIC_DOMAIN% %PORT%
  >> "%SCRIPT_DIR%start-ngrok-tunnel.cmd" echo pause
)

REM Generate tunnel-client launcher if OpenAI tunnel ID is provided
if defined OPENAI_TUNNEL_ID (
  > "%SCRIPT_DIR%start-openai-tunnel.cmd" echo @echo off
  >> "%SCRIPT_DIR%start-openai-tunnel.cmd" echo REM Start OpenAI tunnel-client
  >> "%SCRIPT_DIR%start-openai-tunnel.cmd" echo REM Make sure tunnel-client is installed (github.com/openai/tunnel-client)
  >> "%SCRIPT_DIR%start-openai-tunnel.cmd" echo tunnel-client --tunnel-id %OPENAI_TUNNEL_ID% --local-port %PORT%
  >> "%SCRIPT_DIR%start-openai-tunnel.cmd" echo pause
)

echo Generated:
echo   %SCRIPT_DIR%start-review-mcp.cmd
if defined NGROK_STATIC_DOMAIN (
  echo   %SCRIPT_DIR%start-ngrok-tunnel.cmd
  echo.
  echo To use ngrok static domain:
  echo   1. Run: start-ngrok-tunnel.cmd
  echo   2. In another terminal, run: start-review-mcp.cmd
  echo   3. ChatGPT connector: https://%NGROK_STATIC_DOMAIN%/mcp
) else if defined OPENAI_TUNNEL_ID (
  echo   %SCRIPT_DIR%start-openai-tunnel.cmd
  echo.
  echo To use OpenAI tunnel:
  echo   1. Run: start-openai-tunnel.cmd
  echo   2. In another terminal, run: start-review-mcp.cmd
  echo   3. ChatGPT will connect via tunnel_id
) else (
  echo.
  echo URL options:
  echo   1. Own domain: Set REVIEW_PUBLIC_URL=https://mcp.example.com
  echo   2. ngrok static: Set REVIEW_NGROK_STATIC_DOMAIN=xxx.ngrok-free.app
  echo   3. OpenAI tunnel: Set REVIEW_OPENAI_TUNNEL_ID=tunnel_xxx
)
echo.
echo Token file:
echo   %TOKEN_FILE%
if defined PUBLIC_URL (
  echo Connector endpoint: %PUBLIC_URL%/mcp
) else if defined NGROK_STATIC_DOMAIN (
  echo Connector endpoint: https://%NGROK_STATIC_DOMAIN%/mcp
) else (
  echo Connector endpoint: https://your-public-host/mcp
)

endlocal
