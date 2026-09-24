@echo off
setlocal
pushd "%~dp0"
npx --yes skills add . --skill chatgpt-agent --skill chatgpt-agent-setup
set "EXIT_CODE=%ERRORLEVEL%"
popd
if not "%EXIT_CODE%"=="0" exit /b %EXIT_CODE%
echo Skills installed. Run $chatgpt-agent-setup; ZIP is the default, and MCP starts only when you choose it.
endlocal
