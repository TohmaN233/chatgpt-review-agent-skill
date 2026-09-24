# Security contract and limits

See [capability profiles and task leases](../CAPABILITY_PROFILES.md) for the
available task permissions.

## Trust and isolation

The local account, installed bridge code and authorized Host are trusted. Model
output, repository content, HTTP requests and arbitrary Connector clients are
not authority to expand a lease. This bridge is not an OS sandbox. A same-owner
process can change local state or race workspace directories; repository tests
can execute arbitrary code. Run hostile projects in separate OS isolation.

The control credential stays in per-user state outside every exposed root and is
never an MCP bearer. The persistent workspace MCP bearer is stored separately
in the same private per-user state directory. POSIX files/directories use
private permissions; Windows relies on the per-user directory's NTFS ACL. Do
not place that state in a shared or synchronized directory. Temporary OAuth
material is hashed in memory, with bounded registration/session/token tables
and authentication work limits.

## OAuth and transport

Registered redirects match exactly. DCR records a client and callback; the direct authorization request still validates the registered callback, workspace, scope, and PKCE. In persistent-Bearer mode, `/authorize` accepts only a registered HTTPS ChatGPT
Connector callback. It validates the workspace resource/scope and PKCE S256,
then issues a single-use code directly so the configured Connector can finish
without a pairing screen. DCR clients and the workspace Bearer survive Bridge
restarts; the client registry remains local to this workspace. Codes last 60
seconds. The token endpoint returns the persistent MCP Bearer only after the authorization code, registered client, exact redirect, PKCE verifier, and workspace audience all match. Both the Bearer and client registry survive Bridge restarts; sessions, task
leases and receipts are recreated after restart. The Bearer is rotated
atomically when ChatGPT revokes it or the workspace owner explicitly disconnects
it. Owner disconnect works independently of model calls.

The advertised issuer is fixed by Host configuration, never forwarded headers.
Public origins must use HTTPS. The process listens on loopback behind a tunnel;
Host/Origin checks, bounded request framing and duplicate-parameter rejection
also apply. It implements the advertised MCP JSON/HTTP subset, not all protocol
versions or all OAuth client registration mechanisms. DCR is the supported
registration path. Third-party interoperability needs real provider acceptance.
`https://chatgpt.com` is accepted as Origin on Connector OAuth endpoints
(`/register`, `/authorize`, `/token`, `/revoke`) because ChatGPT submits those
requests from its Connector flow. `Origin: null` is accepted only on
`/authorize`. Exact Host validation still applies. Control and MCP endpoints
continue rejecting these origins.

Restart intentionally revokes sessions, leases and receipts while preserving
the workspace Bearer and trusted client bindings. The Connector can continue
using that Bearer after restart. Explicit OAuth revocation rotates it and
requires the Connector to authorize again.

## Mutations and evidence

Profiles cap authority. Each mutation checks a live session-and-task lease, its
256-bit task capability and the current Git base. The host returns the raw task
capability only to local control when a grant is issued; it stores only the
SHA-256 hash. Every mutation carries it, but the operation journal excludes it.
Capabilities differ per task even when ChatGPT reuses one Connector session.
`workspace_info` reports the host ceiling, never the active task grant. Source writes additionally check primary root,
allowed path and expected file hash. Bridge writes are serialized, staged and
checked again before commit; creation is no-overwrite. Source modes are preserved
on replacement. Symlinks, junctions, hard links, devices, traversal, Windows ADS
and ambiguous platform path spellings are rejected. Packet and MCP load the
same sensitivity/custom-ignore policy.

The private SQLite journal admits an operation before execution and records its
result after execution. A crash in between has **unknown outcome**, not failed
or successful by assumption; automatic retries are blocked. Host `checkpoint`
returns those states. The Host must inspect an unknown outcome and choose a new
operation ID only after reconciliation. The journal stores request hashes, not
raw source bodies; there is no automatic pruning of recovery evidence.

Named validation requires an explicit per-task name. It has a deadline, lease
revocation checks, a 1 MB output capture ceiling, 40 KB display limit and visible
workspace snapshot budgets. Before/after content hashes include untracked files.
Private-key blocks, common credentials, known secret environment values and
host paths are sanitized **before** output tailing. This is best-effort redaction,
not a guarantee that every arbitrary secret is recognizable. Windows process
cleanup uses taskkill; it is not Job Object containment, and detached descendants
are outside the stated sandbox boundary. No arbitrary shell command is accepted.

## Verification provenance

A locally created challenge must be observed by an authenticated non-diagnostic
`workspace_info` call. The owner confirms its server-stored receipt. Receipts
expire and bind workspace, endpoint, boot, session and tool. Diagnostic probes,
caller timestamps, invented IDs and prior-boot receipts cannot establish ready.
This authenticates a Connector session, not its brand: a simulated client must
never be represented as a real ChatGPT UI check.

## Migration/rollback

Persistent-Bearer OAuth authorization uses the automatic ChatGPT callback flow.
The MCP bearer is separate from the local owner-control credential and is
returned by `/token` after exact callback, workspace, and PKCE checks. After
upgrading, use `$chatgpt-agent-setup` to configure and verify
the workspace Connector. Install `chatgpt-agent` and
`chatgpt-agent-setup`; ZIP mode needs no Bridge. If rolling back, stop the new
server first and use Packet-only operation: do not republish the old
unauthenticated approval flow. Keep the journal for outcome reconciliation.

Packet output arguments must name canonical, non-linked directories; use the
canonical workspace path returned by local setup rather than an OS directory
symlink alias. Raw output links are rejected before identity normalization.
After that check, canonical identities prevent Windows short/long-path aliases
from bypassing output/evidence or output/output collision checks. Local owner
HTTP uses numeric loopback directly; no proxy, TLS setup or reverse DNS is
needed for that channel. Public HTTPS requests still verify TLS normally.
