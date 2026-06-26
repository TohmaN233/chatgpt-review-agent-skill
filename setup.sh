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

echo "Generated:"
echo "  $SCRIPT_DIR/start-review-mcp.sh"
echo "Token file:"
echo "  $TOKEN_FILE"
echo "Connector endpoint: ${PUBLIC_URL:-https://your-public-host}/mcp"
