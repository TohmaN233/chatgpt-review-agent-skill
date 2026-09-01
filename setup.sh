#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEFAULT_REPO=$(pwd)
DEFAULT_SKILLS="${CODEX_HOME:-$HOME/.codex}/skills"

REPO_ROOT=${REVIEW_REPO_ROOT:-$DEFAULT_REPO}
SKILLS_ROOT=${REVIEW_SKILLS_ROOT:-$DEFAULT_SKILLS}
PUBLIC_URL=${REVIEW_PUBLIC_URL:-}
HOST=${REVIEW_HOST:-127.0.0.1}
PORT=${REVIEW_PORT:-8765}
EDIT=${REVIEW_ENABLE_EDIT:-n}
TOKEN_FILE=${REVIEW_TOKEN_FILE:-"$SCRIPT_DIR/.review-mcp-token"}
NGROK_STATIC_DOMAIN=${REVIEW_NGROK_STATIC_DOMAIN:-}
OPENAI_TUNNEL_ID=${REVIEW_OPENAI_TUNNEL_ID:-}

EDIT_ARG=
case "$EDIT" in
  y|Y|yes|YES) EDIT_ARG="--enable-edit" ;;
esac

CMD="python \"$SCRIPT_DIR/mcp_server.py\" --root \"$REPO_ROOT\" --root \"$SKILLS_ROOT\" --host \"$HOST\" --port \"$PORT\" --token-file \"$TOKEN_FILE\""
if [ -n "$PUBLIC_URL" ]; then
  CMD="$CMD --public-url \"$PUBLIC_URL\""
fi
if [ -n "$EDIT_ARG" ]; then
  CMD="$CMD $EDIT_ARG"
fi

cat > "$SCRIPT_DIR/start-review-mcp.sh" <<EOF
#!/usr/bin/env sh
$CMD
EOF
chmod +x "$SCRIPT_DIR/start-review-mcp.sh"

# Generate ngrok launcher if static domain is provided
if [ -n "$NGROK_STATIC_DOMAIN" ]; then
  cat > "$SCRIPT_DIR/start-ngrok-tunnel.sh" <<EOF
#!/usr/bin/env sh
# Start ngrok with your static domain
# Make sure ngrok is installed and authenticated
ngrok http --url=$NGROK_STATIC_DOMAIN $PORT
EOF
  chmod +x "$SCRIPT_DIR/start-ngrok-tunnel.sh"
fi

# Generate tunnel-client launcher if OpenAI tunnel ID is provided
if [ -n "$OPENAI_TUNNEL_ID" ]; then
  cat > "$SCRIPT_DIR/start-openai-tunnel.sh" <<EOF
#!/usr/bin/env sh
# Start OpenAI tunnel-client
# Make sure tunnel-client is installed (github.com/openai/tunnel-client)
tunnel-client --tunnel-id $OPENAI_TUNNEL_ID --local-port $PORT
EOF
  chmod +x "$SCRIPT_DIR/start-openai-tunnel.sh"
fi

echo "Generated:"
echo "  $SCRIPT_DIR/start-review-mcp.sh"
if [ -n "$NGROK_STATIC_DOMAIN" ]; then
  echo "  $SCRIPT_DIR/start-ngrok-tunnel.sh"
  echo ""
  echo "To use ngrok static domain:"
  echo "  1. Run: ./start-ngrok-tunnel.sh"
  echo "  2. In another terminal, run: ./start-review-mcp.sh"
  echo "  3. ChatGPT connector: https://$NGROK_STATIC_DOMAIN/mcp"
elif [ -n "$OPENAI_TUNNEL_ID" ]; then
  echo "  $SCRIPT_DIR/start-openai-tunnel.sh"
  echo ""
  echo "To use OpenAI tunnel:"
  echo "  1. Run: ./start-openai-tunnel.sh"
  echo "  2. In another terminal, run: ./start-review-mcp.sh"
  echo "  3. ChatGPT will connect via tunnel_id"
else
  echo ""
  echo "URL options:"
  echo "  1. Own domain: Set REVIEW_PUBLIC_URL=https://mcp.example.com"
  echo "  2. ngrok static: Set REVIEW_NGROK_STATIC_DOMAIN=xxx.ngrok-free.app"
  echo "  3. OpenAI tunnel: Set REVIEW_OPENAI_TUNNEL_ID=tunnel_xxx"
fi
echo ""
echo "Token file:"
echo "  $TOKEN_FILE"
if [ -n "$PUBLIC_URL" ]; then
  echo "Connector endpoint: ${PUBLIC_URL}/mcp"
elif [ -n "$NGROK_STATIC_DOMAIN" ]; then
  echo "Connector endpoint: https://${NGROK_STATIC_DOMAIN}/mcp"
else
  echo "Connector endpoint: https://your-public-host/mcp"
fi
