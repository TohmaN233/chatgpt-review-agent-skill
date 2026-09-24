# Local control plane

`agentctl.py` owns deterministic local setup/start/stop/status/doctor, endpoint
and capability-ceiling changes, bearer credential lifecycle, task leases and
receipt confirmation.
The setup Skill owns the browser/provider steps, not authentication policy.

State, the local owner credential and the persistent MCP bearer live in per-user
OS state storage, outside all exposed workspaces. `CHATGPT_AGENT_HOME` overrides
that location. State writes
are atomic; lifecycle commands use an OS advisory writer lock. HTTP control is
loopback-only, accepts no browser Origin, requires the private owner credential,
and validates the actual workspace/boot identity. It never accepts an old PID as
sufficient authority to stop an unrelated service.

Start performs an authenticated identity probe. Stop sends its response before
shutdown, revokes leases and lets active bridge mutation/validation cleanup
finish. Endpoint/profile changes stop the old service before publishing the new
configuration and invalidate verification.

Doctor separately verifies local control, actual authenticated MCP requests,
OAuth metadata, configured public identity and a live confirmed smoke receipt.
`local_ready` is not `ready`. A diagnostic token cannot mint a connector receipt
or receive a task lease. Restart revokes sessions/leases/receipts but preserves
the MCP bearer, trusted OAuth client bindings and operation journal. See [security](SECURITY.md) and
[setup](../setup/ONE_COMMAND.md).
