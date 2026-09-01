# ChatGPT Review Agent Skill

Use ChatGPT as an external code reviewer from Codex.

[中文说明](README.zh-CN.md)

**Packet review is the default.** MCP connector review needs ChatGPT **Developer mode**.

- **Packet review (default):** works without MCP. Codex packages code, sends/uploads it to any ChatGPT reviewer model, then captures the reply back to local markdown.
- **MCP connector review:** lets ChatGPT read selected local files through a tiny MCP server. Personal connectors require **Apps → Advanced settings → Developer mode**. Without it, ChatGPT returns `FORBIDDEN: This conversation does not support developer MCPs` even if the local server is healthy.

For MCP tool calls, use High/extra-high. Pro is for packet review.

## Before Proceeding

You do not need MCP to use this skill. Start with packet review.

If you only want GPT as a review agent, or do not want connector setup:

1. Ask Codex to use `$chatgpt-review-agent`.
2. Codex builds a packet zip from the relevant files.
3. Codex uploads/sends it to ChatGPT in the side browser.
4. ChatGPT replies.
5. Codex captures the newest reply and saves it locally.

This is the default and is usually enough for external GPT review. Set up MCP when you want ChatGPT to read local files through a connector; turn on Developer mode first.

## Install The Skill

Recommended:

```bash
npx skills add TohmaN233/chatgpt-review-agent-skill
```

Alternative (Codex, manual copy): copy the skill folder into your Codex skills directory:

```bash
cp -r skills/chatgpt-review-agent ~/.codex/skills/
```

Windows PowerShell:

```powershell
Copy-Item -Recurse .\skills\chatgpt-review-agent $env:USERPROFILE\.codex\skills\
```

Restart Codex after installing.

Use it like:

```text
Use $chatgpt-review-agent to ask ChatGPT Pro to review this change and save the review markdown locally.
```

## Packet Review

This is the default path. Use it for any GPT reviewer model, including when MCP exists but ChatGPT will not actually call tools.

Paths such as `<skill-dir>`, `<repo-root>`, and `<relative/file.py>` are placeholders. A coding agent should resolve the real paths from its current workspace and the skill source location.

Build a packet:

```bash
python <skill-dir>/scripts/build_review_packet.py \
  --repo <repo-root> \
  --out .chatgpt-review/review-packet.md \
  --zip .chatgpt-review/review-packet.zip \
  --goal "Review this change for bugs and missing tests." \
  --file <relative/file.py> \
  --dir tests
```

The zip includes `review-packet.md` and supporting files. ChatGPT can read uploaded zip contents, so zip is preferred for multi-file reviews.

Then Codex should:

1. Open ChatGPT in the Codex side browser/tab.
2. Select Pro, or any desired tool-less reviewer.
3. Upload `.chatgpt-review/review-packet.zip`.
4. Ask ChatGPT to review only the packet and not call tools.
5. Wait for generation to finish.
6. Save the newest assistant reply, usually to `.chatgpt-review/review.md`.

## MCP Connector Review

Use this only when you explicitly want ChatGPT to read local files through a connector, and only after a real smoke test works in the current ChatGPT conversation. If the smoke test is blocked, stop and use packet. Do not keep retrying MCP.

The flow is:

1. In ChatGPT, enable **Developer mode** (**Apps → Advanced settings**). Personal connectors need this.
2. Start the local MCP server.
3. Expose it through an HTTPS URL.
4. Create a ChatGPT app/connector.
5. Select that app in the ChatGPT composer with the `+` button.
6. Smoke test `list_allowed_roots`.
7. Only then ask ChatGPT to review files.

### One-Time Guided Setup

For beginner-friendly setup, give `AGENT_SETUP_PROMPT.md` to Codex.

If the current Codex turn cannot show choice prompts, the agent should tell the user:

```text
请先单独输入 /plan 并回车。
进入 Plan mode 后，再发送：引导设置 MCP。
```

In Plan mode, Codex should infer:

- current repo root
- Codex skills root
- OS
- sensible port, usually `8765`
- public HTTPS URL, if already provided
- whether source editing should be enabled

The setup helpers are not questionnaires. They read environment variables and generate one-click launchers.

Windows:

```cmd
setup.cmd
```

macOS/Linux:

```bash
sh setup.sh
```

Generated launchers:

```text
start-review-mcp.cmd
start-review-mcp.sh
```

The generated launcher uses a persistent token file by default:

```text
.review-mcp-token
```

Keep this file. If it is deleted, ChatGPT may need connector re-authentication.

Useful setup variables:

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

- review write artifacts enabled under `.chatgpt-review/`
- whitelisted shell tool enabled
- source editing disabled
- token file at `<this-repo>/.review-mcp-token`

Enable direct source edits only when you explicitly want the ChatGPT-side model to modify files:

```text
REVIEW_ENABLE_EDIT=yes
```

### Manual Server Start

Run from this repo:

```bash
python mcp_server.py \
  --root <repo-root> \
  --root <skills-root> \
  --host 127.0.0.1 \
  --port 8765 \
  --public-url <public-url> \
  --token-file .review-mcp-token
```

Add `--enable-edit` only if you want ChatGPT to write source files.

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

Expected:

```json
{"status":"ok","root":"<repo-root>"}
```

## HTTPS URL Options

**THE URL RULE:**

ChatGPT binds your connector to a specific hostname (or OpenAI `tunnel_id`). **If the URL changes, you must delete the old app and re-add it in ChatGPT.** Use a stable URL to add the connector once.

- Random trycloudflare / random ngrok = re-add every time → **debug-only**
- Stable hostname = add once, keep `.review-mcp-token`

Deleting `.review-mcp-token` may force re-authentication but not a new app if the URL is unchanged.

### Three Stable Paths

Use these in order based on your situation:

#### 1. Own Domain (Best if you have one)

Use a **Cloudflare named tunnel** or similar service with your own domain.

**Setup:**

1. Put the domain on Cloudflare, or use a domain already managed by Cloudflare.
2. Open Cloudflare Zero Trust.
3. Go to **Networks → Tunnels**.
4. Create or reuse a tunnel.
5. Install `cloudflared` as a machine service. On Windows:

```cmd
cloudflared.exe service install <token>
```

6. In the tunnel, add a **Public Hostname**:

```text
Subdomain: mcp
Domain: example.com
Type: HTTP
URL: http://127.0.0.1:8765
```

**MCP Server:**

Start with `--public-url` set to the base URL (no `/mcp` suffix):

```bash
python mcp_server.py \
  --root <repo-root> \
  --root <skills-root> \
  --host 127.0.0.1 \
  --port 8765 \
  --public-url https://mcp.example.com \
  --token-file .review-mcp-token
```

**ChatGPT connector endpoint:**

```text
https://mcp.example.com/mcp
```

**Checks:**

```bash
curl https://mcp.example.com/.well-known/oauth-authorization-server
curl https://mcp.example.com/mcp
```

`/mcp` rejecting unauthenticated requests is expected; it means the route reaches the server.

**Troubleshooting:**

- If Cloudflare says an A, AAAA, or CNAME record already exists, delete the conflicting DNS record or choose another subdomain.
- If Cloudflare shows `1016`, the hostname is not routed to a live tunnel. Fix the tunnel Public Hostname to point to `http://127.0.0.1:8765`.

#### 2. No Domain (Free ngrok static)

Use an **ngrok free static assigned domain** (`xxx.ngrok-free.app`). Others already use this path (DevSpace, Pieces, ROS-MCP, ChatGPT Apps tutorials).

**Setup:**

1. Sign up for a free ngrok account at https://ngrok.com
2. Find your static domain in the ngrok dashboard (usually `xxx.ngrok-free.app`)
3. Install ngrok and authenticate with your authtoken

**Run ngrok:**

```bash
ngrok http --url=xxx.ngrok-free.app 8765
```

Replace `xxx` with your actual assigned static domain.

**MCP Server:**

```bash
python mcp_server.py \
  --root <repo-root> \
  --root <skills-root> \
  --host 127.0.0.1 \
  --port 8765 \
  --public-url https://xxx.ngrok-free.app \
  --token-file .review-mcp-token
```

**ChatGPT connector endpoint:**

```text
https://xxx.ngrok-free.app/mcp
```

**Important:** Do NOT use `ngrok http 8765` without `--url` (generates random URLs). Always use `--url=xxx.ngrok-free.app` with your static domain.

#### 3. OpenAI Secure MCP Tunnel (Optional, 2026-05+)

Use **OpenAI Connection Tunnel** with `tunnel_id`. Your laptop runs `github.com/openai/tunnel-client` outbound; no public hostname needed.

**Setup:**

1. Create a Connection Tunnel in ChatGPT (Apps → Create Connection Tunnel)
2. Note the `tunnel_id`
3. Install and run `tunnel-client`:

```bash
git clone https://github.com/openai/tunnel-client
cd tunnel-client
# Follow tunnel-client setup instructions
tunnel-client --tunnel-id <your-tunnel-id> --local-port 8765
```

**MCP Server:**

```bash
python mcp_server.py \
  --root <repo-root> \
  --root <skills-root> \
  --host 127.0.0.1 \
  --port 8765 \
  --token-file .review-mcp-token
```

**Note:** OpenAI tunnels have workspace and RBAC limits. Not suitable for public plugin store submission. Used by projects like RepoRelay.

### Debug-Only: trycloudflare

**Do NOT use for production.** Random `trycloudflare.com` URLs rotate on every restart. You must re-add the ChatGPT app every time. Also has SSE stream issues.

```bash
cloudflared tunnel --url http://127.0.0.1:8765
```

This generates a random URL like `https://random-word-1234.trycloudflare.com`. Use only for quick testing.

### Do NOT Use: workers.dev

Cloudflare Workers cannot access your local disk. Do not try to deploy `mcp_server.py` to workers.dev.

## ChatGPT App / Connector Setup

Personal/custom connectors will not call tools until **Developer mode** is on.

In ChatGPT:

Connected review example:

![Codex and ChatGPT connected review example](assets/codex-chatgpt-connected-review.png)

1. Open **Apps**.
2. Open **Advanced settings**.
3. Enable **Developer mode**.
4. Create an app.
5. Give it a name that contains `connect`, for example:

```text
connectcodex
```

The `connect` name is not a protocol requirement, but it makes the app easy to find in the composer `+` menu and matches the tested workflow.

6. For the connector/MCP URL, enter:

```text
https://repo.example.com/mcp
```

7. Complete the OAuth flow.
8. Refresh/rescan tools if ChatGPT offers that action.
9. In the ChatGPT composer, click the lower-left `+`.
10. Select your app, for example `connectcodex`.
11. Use a model that can call tools, usually High/extra-high.

Smoke prompt:

```text
Use the selected connector only. Smoke test: call list_allowed_roots only. Reply whether a real tool call happened and paste the returned roots or exact error. Do not call any other tool.
```

Pass condition:

- ChatGPT UI shows a real tool call, and
- the reply returns roots such as `<repo-root>` and `<skills-root>`.

If Pro cannot call tools, use packet review.

If ChatGPT gets stuck looking for tools, reselect the app from the composer `+` menu and retry once.

## MCP Tools

The bundled server exposes:

- `list_allowed_roots`
- `tree`
- `read_text`
- `search_text`
- `write_review`
- `list_review_artifacts`
- `run_command`
- `write_text` only with `--enable-edit`

Safety limits:

- roots must be explicitly allowed with `--root`
- review writes are confined to `.chatgpt-review/`
- source editing requires `--enable-edit`
- shell is a fixed allowlist
- secret-ish paths such as `.env`, private keys, `.git`, and `node_modules` are blocked from all file operations
- `tree`, `read_text`, and `search_text` share the same DENY_NAMES and DENY_GLOBS allowlist
- `tree`, `read_text`, and `search_text` are capped
- stdlib-only Python, no package install

Allowed `run_command` values:

```text
git status --short
git diff --stat
git diff
python -m pytest
npm test
```

## Troubleshooting

**Error fetching OAuth configuration**

- Check `<public-url>/.well-known/oauth-authorization-server`.
- Start the server with `--public-url <public-url>`.
- Check the tunnel routes to `http://127.0.0.1:8765`.

**Message stream error while looking for tools**

- Confirm the server is alive with `/health`.
- Reselect the connector app from the ChatGPT composer `+` menu.
- Retry the smoke prompt once.

**Cloudflare 1016**

- The public hostname is not routed to the tunnel target.
- Fix the tunnel Public Hostname service URL: `http://127.0.0.1:8765`.

**Pro cannot call tools**

Expected in some ChatGPT surfaces. Use packet review.

**FORBIDDEN: This conversation does not support developer MCPs**

First check ChatGPT **Apps → Advanced settings → Developer mode**. Personal connectors need it. If Developer mode is already on, this is conversation/account policy, not a local MCP bug. Use packet in that conversation.

**This tool call was blocked by OpenAI's safety checks**

Confirm Developer mode is on and the connector is selected. If the local server still has no matching `/mcp` request, use packet for that turn.

**Tool call appears fake**

Treat it as unverified unless the ChatGPT UI shows a tool call or the MCP server log shows a matching `/mcp` request. Then use packet review.

## Files

```text
AGENT_SETUP_PROMPT.md
mcp_server.py
setup.cmd
setup.sh
skills/chatgpt-review-agent/SKILL.md
skills/chatgpt-review-agent/scripts/build_review_packet.py
skills/chatgpt-review-agent/references/setup.md
skills/chatgpt-review-agent/references/browser-workflows.md
```
