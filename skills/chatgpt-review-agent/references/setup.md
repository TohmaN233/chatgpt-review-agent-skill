# ChatGPT Review Agent Setup

Use this only when the user wants ChatGPT to read through an MCP connector. In the tested ChatGPT setup, Pro cannot call MCP connector tools; use packet review for Pro.

## The Generic Shape

1. Run a local or remote MCP server that exposes review tools.
2. Expose it to ChatGPT with HTTPS if ChatGPT cannot reach localhost.
3. Add it as a ChatGPT App/Connector.
4. In ChatGPT, click the composer `+` button, usually at the lower-left of the input box, and select the user's connector app. The app name often contains `connect`.
5. Smoke test one harmless read-only/status tool before review.

If any step fails, use packet review. Packet review needs no MCP.

## MCP Server Requirement

Start any MCP server that exposes the minimum tools needed for review. This repo includes `mcp_server.py`, a tiny stdlib server. Useful tools include:

- list allowed roots or current workspace
- list files or tree a small directory
- read exact file text
- optionally search narrow paths

Avoid write tools for review unless the user explicitly wants the external agent to mutate files.

Treat `<repo-root>`, `<skills-root>`, and `<public-url>` as placeholders. The agent should infer actual paths from the current workspace, environment, and installed skill location; do not make the user manually locate standard directories.

If the MCP server is local HTTP, it will usually look like:

```text
http://127.0.0.1:PORT/mcp
```

From this repo:

```bash
python mcp_server.py --root <repo-root> --root <skills-root> --host 127.0.0.1 --port 8765
```

For beginner-friendly setup, use `AGENT_SETUP_PROMPT.md`. If the current turn cannot show Codex choice prompts, first tell the user to enter `/plan` by itself and press Enter, then send `引导设置 MCP` after entering Plan mode. In Plan mode, infer the current project root, Codex skills directory, OS, and sensible defaults, then confirm or change public URL, source-edit permission, and port before running setup.

The setup helpers are not questionnaires. They read environment variables and generate native one-click launch scripts.

Windows:

```cmd
setup.cmd
```

macOS/Linux:

```bash
sh setup.sh
```

`setup.cmd` writes `start-review-mcp.cmd`. `setup.sh` writes `start-review-mcp.sh`.

Useful environment variables:

```text
REVIEW_REPO_ROOT=<repo-root>
REVIEW_SKILLS_ROOT=<skills-root>
REVIEW_PUBLIC_URL=<public-url>
REVIEW_HOST=127.0.0.1
REVIEW_PORT=8765
REVIEW_ENABLE_EDIT=n
REVIEW_TOKEN_FILE=<local-token-file>
```

Defaults:

- `.chatgpt-review/` write tools enabled
- whitelisted shell command tool enabled
- source edit tool disabled
- token file is `<repo-containing-mcp-server>/.review-mcp-token`

The generated `start-review-mcp` script passes `--token-file`. Keep that file across restarts so ChatGPT's OAuth token remains valid; delete it only when you intentionally want to force connector re-authentication.

Enable direct source editing only when the user explicitly wants the ChatGPT-side model to modify repo files:

```bash
python mcp_server.py --root <repo-root> --enable-edit
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:PORT/health
```

Expected: `status=ok`.

## Example: Stable Cloudflare Domain

Use this when ChatGPT needs a public MCP URL and random trycloudflare URLs are too annoying.

1. Create or reuse a Cloudflare Tunnel.
2. Route a hostname such as `<public-url>` to the local MCP HTTP server.
3. The ChatGPT MCP endpoint is:

```text
<public-url>/mcp
```

Useful checks:

```powershell
Invoke-RestMethod <public-url>/.well-known/oauth-authorization-server
Invoke-WebRequest <public-url>/mcp
```

The bare `/mcp` request may reject unauthenticated access; that still proves the route reaches the service.

If Cloudflare shows `1016`, the hostname is routed to a missing tunnel target or DNS/tunnel routing is wrong. Fix the tunnel public hostname, not ChatGPT.

## ChatGPT Connector

In ChatGPT:

1. Add an MCP connector using `<public-url>/mcp`.
2. Refresh tools after editing the connector.
3. In the ChatGPT composer, click the `+` button, usually at the lower-left of the input box.
4. Select the user's connector app. It often has `connect` in the name, but the exact name is user-defined.
5. Use `High`/`extra-high` for tool calls if Pro does not expose tools.

If you replace one MCP server with another:

- Same public URL, same `/mcp` endpoint: usually no new ChatGPT connector is needed; restart the server, then refresh/rescan tools in ChatGPT.
- Different public URL or different connector name: edit the existing connector endpoint or create a new connector, then complete OAuth and refresh tools.
- OAuth metadata or auth behavior changed: re-authenticate the connector.

Smoke prompt:

```text
Use the selected connector. Call one harmless read-only/status tool only.
Reply whether a real tool call happened and what it returned.
```

If this fails on Pro but works on `High`/`extra-high`, that is a ChatGPT surface limitation. Use the packet workflow for Pro.

## Known Operational Rules

- Prefer exact `read_text` over broad search.
- Keep `tree` small, usually `max_entries <= 30`.
- Verify real tool calls with the MCP server audit log when available.
- If MCP session state gets weird, stop retry loops; restart the local MCP server and try one smoke call.
