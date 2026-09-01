# Agent-Led Setup Prompt

Use this prompt when asking an AI coding agent to set up the ChatGPT Review Agent MCP bridge for a user.

```text
Set up the ChatGPT Review Agent MCP bridge for this machine.

Drive this as a Codex-guided setup with interactive choices. Do not silently auto-run setup.

First step: if this turn is not already in Plan mode or cannot show Codex choice prompts, tell the user:

```text
Use /plan 。
After change to Plan mode, send ：Set review-agent MCP。
```

Then stop. Do not continue setup in the current turn.

Infer everything obvious, then call Codex's user-input/choice UI to let me confirm or change the setup. Ask one short choice at a time unless the UI naturally supports multiple compact questions.

Required choice steps:
1. Public HTTPS URL strategy:
   - own domain via Cloudflare named tunnel (recommended if you have a domain)
   - ngrok free static domain (recommended if no domain)
   - OpenAI Secure MCP Tunnel (optional, workspace-scoped)
   - enter another URL
   - skip public URL for now
2. Source edits:
   - keep disabled (recommended)
   - enable ChatGPT-side source editing
3. Local port:
   - use 8765 (recommended)
   - enter another port

Only after those choices are confirmed, run setup.

Infer:
- project root from the current workspace
- Codex skills directory from CODEX_HOME or ~/.codex/skills
- operating system from the shell

Recommend:
- own domain with Cloudflare named tunnel if available
- ngrok free static domain if no domain
- keep ChatGPT-side source edits disabled
- use local port 8765

Then set environment variables and run the appropriate helper:
- Windows: setup.cmd
- macOS/Linux: sh setup.sh

Set:
- REVIEW_REPO_ROOT to the current workspace
- REVIEW_SKILLS_ROOT to CODEX_HOME/skills or ~/.codex/skills
- For own domain: REVIEW_PUBLIC_URL (e.g., https://mcp.example.com)
- For ngrok static: REVIEW_NGROK_STATIC_DOMAIN (e.g., xxx.ngrok-free.app)
- For OpenAI tunnel: REVIEW_OPENAI_TUNNEL_ID (e.g., tunnel_xxx)
- REVIEW_HOST and REVIEW_PORT if needed
- REVIEW_ENABLE_EDIT=n unless user explicitly ask for ChatGPT-side source edits
- REVIEW_TOKEN_FILE only if the user wants a custom token location; otherwise let setup use its default `.review-mcp-token`

Then run:
- Windows: setup.cmd
- macOS/Linux: sh setup.sh

If I provide a public HTTPS URL for a tunnel, include it.
Do not enable source editing unless I explicitly ask for ChatGPT to edit files.

After setup, tell me:
1. which start script was generated (and tunnel scripts if applicable)
2. the ChatGPT connector endpoint or tunnel setup instructions
3. that ChatGPT **Apps → Advanced settings → Developer mode** must be on; personal connectors cannot call tools without it
4. that user must refresh/rescan tools
5. that user must click + in the ChatGPT composer and choose my connector app, often named with "connect"
6. the smoke prompt: "Use the selected connector. Call list_allowed_roots only."
7. the URL stability rule: ChatGPT binds the connector to the hostname. If the URL changes, the app must be re-added. Keep `.review-mcp-token` for stable URLs.
```
