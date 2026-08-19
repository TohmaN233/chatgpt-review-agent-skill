---
name: chatgpt-review-agent
description: "Use ChatGPT in the Codex side browser/tab as a review agent for local artifacts. Default is packet review. Use MCP only if the user explicitly asks and a smoke test works. Covers code, papers, drafts, Pro and High/extra-high reviewers."
---

# ChatGPT Review Agent

Use this when the user wants ChatGPT High/extra-high or Pro to review local artifacts from Codex: code, papers, writing drafts, logs, or bundled evidence.

The ChatGPT Codex side browser/tab must be open for automation. Do not ask the user to copy/paste unless browser control is unavailable.

## Choose The Path

**Packet path is the default.**

- **Packet path (default):** Build a packet, upload it, capture the reply, save the markdown.
- **MCP connector path:** Only if the user explicitly asks, and only after one `list_allowed_roots` smoke test succeeds.

## Setup Reference

If the user explicitly wants connector review and MCP is not already working, read `references/setup.md`. It explains the generic MCP connector shape and a stable HTTPS tunnel option.

For packet upload or browser capture, read `references/browser-workflows.md` first. It contains the required `.zip` upload workflow and the preferred review-capture method.

## Packet Builder

Prefer the bundled script when preparing evidence for Pro or any GPT review:

```bash
python <skill-dir>/scripts/build_review_packet.py --repo . --out .chatgpt-review/review-packet.md --zip .chatgpt-review/review-packet.zip --goal "Review this change for bugs and missing tests." --file <relative/file.py> --file tests/test_file.py
```

Treat `<skill-dir>` and `<relative/file.py>` as placeholders. Resolve the installed skill path from this skill's source location; do not ask the user to find it and do not bake a user-specific absolute path into reusable instructions.

The script only includes files under `--repo`, adds line numbers, truncates oversized files, can include small directories with `--dir`, and can produce a `.zip` for ChatGPT upload.

Prefer `.zip` packets for multi-file reviews. ChatGPT can read files inside an uploaded zip attachment, so the packet zip may include both `review-packet.md` and supporting source files.

## Review Prompt Examples

Use a prompt format that matches the artifact. Ask for findings first.

### Code Review

```text
You are acting as an external code reviewer.
Review only the attached packet / provided evidence.

Output:
1. Blocking findings
2. Non-blocking risks
3. Exact next file/line areas Codex should inspect
4. Smallest recommended next check
```

### Academic Paper Review

```text
You are acting as an external academic reviewer.
Review only the attached paper / provided evidence.

Output:
1. Blocking scientific or methodological issues
2. Clarity, structure, and presentation risks
3. Exact sections, figures, equations, or claims to inspect
4. Smallest recommended revision or validation check
```

### Writing Review

```text
You are acting as an external writing editor.
Review only the attached draft / provided evidence.

Output:
1. Blocking issues for the intended audience or purpose
2. Non-blocking clarity, tone, and structure risks
3. Exact passages Codex should revise or inspect
4. Smallest recommended next edit
```

## Packet Path

1. Codex builds a compact packet locally with the script or by hand.
2. Switch ChatGPT to the requested reviewer. Pro is fine. High/extra-high is also fine for packet review.
3. Send/upload the packet. Prefer the generated `.zip`; ChatGPT can inspect the files inside it. Read `references/browser-workflows.md` before uploading; it is the required tutorial for attaching the `.zip` in the ChatGPT browser.
4. Wait until generation is complete.
5. Capture only the newest assistant answer after the latest user packet prompt. Prefer the ChatGPT copy button on that newest assistant response and then read the clipboard text. If using DOM extraction, select the last assistant message after the latest user packet prompt.
6. Save the captured review as a local Markdown file at the requested handoff path, usually:

```text
.chatgpt-review/review.md
```

Before declaring success, verify the local `.md` contains the requested review sections and is not an older turn, a user prompt, or a short interim fragment.

## MCP Connector Path

1. In ChatGPT, click the composer `+` button, usually at the lower-left of the input box.
2. Select the user's MCP-backed App/Connector. Its name often contains `connect`, but the exact name is user-defined.
3. Select a model that can call connector tools, usually `High`/`extra-high` rather than Pro.
4. Smoke test with `list_allowed_roots`. One attempt is enough.
5. After a real tool call, review only narrow paths.
6. Verify the ChatGPT UI showed a tool call, or the MCP server log shows a matching `/mcp` request.
7. Capture the newest assistant answer and save it locally.

Prefer exact file reads over broad search. If listing a tree is needed, keep it narrow and split by directory.

The bundled tiny MCP server exposes `write_review` under `.chatgpt-review/` and a small shell allowlist by default. It does not expose source editing unless started with `--enable-edit`.

## Failure Checks

- MCP is blocked, forbidden, or unverified: report the exact error and use packet.
- ChatGPT is still generating: wait; do not capture interim fragments.
- The saved review is tiny or stale: recapture the newest assistant turn.
