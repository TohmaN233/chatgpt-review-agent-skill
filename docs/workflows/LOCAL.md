# Local review, planning and implementation

Require a matching workspace/boot and verified Connector. Reads do not imply
write permission. The startup profile is the ceiling; the local Host grants a
task lease per authorized session and task. Review/plan may write only task
artifacts. Implement additionally needs explicit source paths and validation
names. Changing roles within the ceiling does not require a restart.

```bash
python agentctl.py grant-task --workspace . --session-id <session> \
  --task-id task-123 --profile implement --path 'src/**' \
  --validation python-unittest --ttl 900 --json
```

Host reads the current base commit, generates expiry/capabilities/lease ID, and
returns a one-time random `task_capability`. Pass that value only to the selected
ChatGPT task through its private task handoff; never put it in workspace files,
packets, reports or journals. The model cannot invent task authority. Every
mutation uses the issued `task_id`, `task_capability` and a stable `operation_id`.
Existing files use a fresh `read_text` hash; new files set `create=true` without
an old hash. Only root-0 can receive source writes. Artifacts are
`.chatgpt-agent/<task-id>/<name>`.

If transport times out, retry the **same** request/operation ID. A recorded result
is returned without execution. If pending/unknown, stop and inspect Host
`checkpoint`; do not invent success or silently issue a new ID. Revoke only this
task with `revoke-task --session-id <session> --task-id task-123` at completion.
A separate review/Packet route remains available for
independent verification. Named validation is explicit repository code execution,
not a restricted OS sandbox. Its structured evidence binds task, base, outcome,
output hash and before/after visible content.
