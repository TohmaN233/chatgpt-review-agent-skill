#!/usr/bin/env sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"
npx --yes skills add . --skill chatgpt-agent --skill chatgpt-agent-setup
printf '%s\n' 'Skills installed. Run $chatgpt-agent-setup; ZIP is the default, and MCP starts only when you choose it.'
