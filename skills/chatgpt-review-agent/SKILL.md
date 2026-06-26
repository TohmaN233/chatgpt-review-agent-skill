---
name: chatgpt-review-agent
description: "Use ChatGPT in the Codex side browser/tab as a review agent for local code: GPT Pro packet review, High/extra-high MCP connector review, no-MCP packet workflows, upload/capture of ChatGPT answers, and saving review markdown back into the repo."
---

# ChatGPT Review Agent

Use this when the user wants ChatGPT High/extra-high or Pro to review local code from Codex.

The ChatGPT Codex side browser/tab must be open for automation. Do not ask the user to copy/paste unless browser control is unavailable.

## Choose The Path

- **Packet path:** Default for Pro, and for any model/session where MCP is unavailable. Codex builds a packet locally, sends/uploads it to the selected ChatGPT reviewer, captures the reply, and writes the handoff file.
- **MCP connector path:** Use only when the target model can call a ChatGPT App/Connector backed by an MCP server. Usually this is `High`/`extra-high`, not Pro. Smoke test before relying on it.

If the user says "Pro review", use packet path. If a non-Pro model cannot call tools, do not keep trying MCP. Use packet path.

## Setup Reference

If MCP is not already working and the user wants connector review, read `references/setup.md`. It explains the generic MCP connector shape and a stable HTTPS tunnel option.

If browser upload/capture details are needed, read `references/browser-workflows.md`.

## Packet Builder

Prefer the bundled script when preparing evidence for Pro or any tool-less GPT review:

```bash
python <skill-dir>/scripts/build_review_packet.py --repo . --out .chatgpt-review/review-packet.md --zip .chatgpt-review/review-packet.zip --goal "Review this change for bugs and missing tests." --file <relative/file.py> --file tests/test_file.py
```

Treat `<skill-dir>` and `<relative/file.py>` as placeholders. Resolve the installed skill path from this skill's source location; do not ask the user to find it and do not bake a user-specific absolute path into reusable instructions.

The script only includes files under `--repo`, adds line numbers, truncates oversized files, can include small directories with `--dir`, and can produce a `.zip` for ChatGPT upload.

Prefer `.zip` packets for multi-file reviews. ChatGPT can read files inside an uploaded zip attachment, so the packet zip may include both `review-packet.md` and supporting source files.

## Review Prompt

Ask ChatGPT for findings first:

```text
You are acting as an external code reviewer.
Review only the attached packet / provided evidence.

Output:
1. Blocking findings
2. Non-blocking risks
3. Exact next file/line areas Codex should inspect
4. Smallest recommended next check
```

## Packet Path

1. Codex builds a compact packet locally with the script or by hand.
2. Switch ChatGPT to Pro, or any desired tool-less reviewer.
3. Send/upload the packet. Prefer the generated `.zip`; ChatGPT can inspect the files inside it.
4. Wait until generation is complete.
5. Capture only the newest assistant answer after the packet prompt.
6. Save it to the requested handoff path, usually:

```text
.chatgpt-review/review.md
```

Before declaring success, verify the saved text contains the requested review sections and is not an older turn, a user prompt, or a short interim fragment.

## MCP Connector Path

1. In ChatGPT, click the composer `+` button, usually at the lower-left of the input box.
2. Select the user's MCP-backed App/Connector. Its name often contains `connect`, but the exact name is user-defined.
3. Select a model that can call connector tools, usually `High`/`extra-high` rather than Pro.
4. Smoke test with `list_allowed_roots` before review.
5. Ask for exact reads or a review over narrow paths only after the smoke test proves a real tool call happened.
6. Verify a real MCP call, not just a plausible answer: the ChatGPT UI must show a tool call, or the MCP server log must show a matching `/mcp` request. If neither is visible, treat MCP as unverified and use packet path.
7. Capture the newest assistant answer and save it locally.

Prefer exact file reads over broad search. If listing a tree is needed, keep it narrow and split by directory.

The bundled tiny MCP server exposes `write_review` under `.chatgpt-review/` and a small shell allowlist by default. It does not expose source editing unless started with `--enable-edit`.

## Failure Checks

- Pro says it has no tool or connector access: expected; use packet path.
- A tool answer appears but no audit entry exists: treat as unverified.
- ChatGPT is still generating: wait; do not capture interim fragments.
- The saved review is tiny or stale: recapture the newest assistant turn.
