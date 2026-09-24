# Capability profiles and task leases

A startup profile is a maximum ceiling, not an OAuth session's write authority.
All authenticated sessions initially read configured roots only. Review/plan
leases add artifact writes; implement leases may add exact path/prefix source
writes and explicitly selected validation names. The local Host grants leases;
no MCP tool can grant or broaden one.

| Profile | Read | Artifacts | Source | Execution |
| --- | --- | --- | --- | --- |
| readonly | yes | no | no | no |
| review / plan | yes | task namespace | no | no |
| implement | yes | task namespace | explicit paths | explicit named validation |

Lease fields are generated/validated by the Host: session ID, task ID, workspace
ID, profile/capabilities, exact file or trailing `/**` path prefixes, validation
names, current base commit, expiry (1–3600 seconds), lease ID and a random
task capability. The local grant returns the capability once; MCP stores only
its hash. Arbitrary glob syntax, other roots, stale commits, unauthorized names
and missing or wrong task capabilities fail closed.

Each mutation supplies the issued task ID, its private task capability and a
stable operation ID. Do not copy the capability into files, packets, artifacts,
reports or user-visible logs. Changing a request under the same operation ID
fails. Completed requests replay the recorded result; pending/unknown requests
require Host inspection.
File replacement also requires `expected_sha256`; creation requires `create=true`
and no previous hash. Artifacts go under `.chatgpt-agent/<task-id>/`.

Use `grant-task`/`revoke-task --task-id ...` to authorize each task's mutations
or named validations. The task role does not determine the profile; the Host
ceiling limits which profiles and capabilities can be granted.
Changing the maximum ceiling with `profile --restart` revokes all sessions and
requires Connector setup again. No lease grants remote GitHub credentials.
Read the [security contract](architecture/SECURITY.md) before granting validation.
