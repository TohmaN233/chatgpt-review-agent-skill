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
1. Public HTTPS URL:
   - use detected/provided URL (recommended when present)
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
- use the detected/provided public HTTPS URL when available
- keep ChatGPT-side source edits disabled
- use local port 8765

Then set environment variables and run the appropriate helper:
- Windows: setup.cmd
- macOS/Linux: sh setup.sh

Set:
- REVIEW_REPO_ROOT to the current workspace
- REVIEW_SKILLS_ROOT to CODEX_HOME/skills or ~/.codex/skills
- REVIEW_PUBLIC_URL if user provided a tunnel URL
- REVIEW_HOST and REVIEW_PORT if needed
- REVIEW_ENABLE_EDIT=n unless user explicitly ask for ChatGPT-side source edits
- REVIEW_TOKEN_FILE only if the user wants a custom token location; otherwise let setup use its default `.review-mcp-token`

Then run:
- Windows: setup.cmd
- macOS/Linux: sh setup.sh

If I provide a public HTTPS URL for a tunnel, include it.
Do not enable source editing unless I explicitly ask for ChatGPT to edit files.

After setup, tell me:
1. which start script was generated
2. the ChatGPT connector endpoint
3. that user must refresh/rescan tools
4. that user must click + in the ChatGPT composer and choose my connector app, often named with "connect"
5. the smoke prompt: "Use the selected connector. Call list_allowed_roots only."
```
