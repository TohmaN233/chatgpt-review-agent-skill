# Task routes

Choose the task role from the requested result, then choose a source route from
where the evidence lives and the user's selected access mode. These are
independent choices; `routes.json` lists compatible role/route pairs. A
compatible pair does not itself grant permission to write or run validation.

| Requested result | Role |
| --- | --- |
| Review a bounded source or verify stated criteria | `reviewer` |
| Answer a reasoning question or make a plan | `advisor` |
| Edit prose or document content | `editor` |
| Implement a software change | `implementer` |

| Source and access | Route |
| --- | --- |
| Supplied or frozen files; current workspace in default ZIP mode | `packet.inspect` |
| Current workspace through explicitly chosen MCP | `local.review` / `local.plan` / `local.implement` |
| Exact remote repository, branch, or PR | `github.review` / `github.implement` |

ZIP packets can return an edit or implementation patch for Codex to apply.
MCP reads begin read-only; MCP writes and named validations require a matching
Host-issued task grant. In `routes.json`, `profile` names the route's
base/default profile. A reviewer may receive a separate Host-issued
`implement`-profile grant for an explicitly authorized named validation when
the Host ceiling permits that profile; this does not change the reviewer role
or `local.review` route. GitHub
implementation requires separate authorization and targets a task branch. An
`alternative_route` is a suggestion only; a route error never switches access
modes automatically.

See [`routes.json`](../routes.json) for compatible role/route pairs and
[`schemas/task-envelope.schema.json`](../schemas/task-envelope.schema.json)
for the task envelope.
