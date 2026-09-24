# Packet Workflows

`packet.inspect` is a first-class route whether or not MCP is configured. It
supports review, checklist verification, analysis and planning, content
editing, and software implementation. ZIP gives ChatGPT no direct workspace
write access: editing and implementation return a patch for Codex to apply.

Build a packet:

```bash
python skills/chatgpt-agent/scripts/build_packet.py \
  --repo . \
  --out .chatgpt-agent/agent-packet.md \
  --manifest .chatgpt-agent/manifest.json \
  --zip .chatgpt-agent/agent-packet.zip \
  --route packet.inspect \
  --role reviewer \
  --goal "Review this change for correctness and missing tests." \
  --file src/example.py \
  --dir tests
```

Choose `--role advisor` for reasoning or planning, `--role editor` for content
edits, and `--role implementer` for software changes.

The packet builder:

- rejects paths outside the repository;
- applies built-in and `.chatgpt-agentignore` exclusions to explicit files and
  directory discovery;
- records original and included SHA-256 values;
- records commit, branch, and dirty state when available;
- omits absolute host paths;
- enforces file, per-file byte, and total byte bounds;
- stores included bytes verbatim in ZIP entries and renders the same selected
  evidence as numbered, fenced text in Markdown.

The selected files and their included bytes in the manifest define the
evidence boundary. No role should claim to have inspected truncated or
unselected source. For explicit criteria, use the reviewer's verification
branch.
