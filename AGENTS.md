# Project rules

- ZIP is the default local workflow. Do not install or start MCP components unless the user explicitly chooses MCP.
- Install `chatgpt-agent` and `chatgpt-agent-setup`; review behavior belongs in the main Skill.
- Keep local workspace tasks and GitHub tasks. Do not add a local/GitHub synchronization route.
- MCP uses one Bridge and one Connector per workspace. Update only the Connector recorded for that workspace.
- On Quick Tunnel restart, update the same workspace Connector when its public URL changes and retain Bearer authorization unless ChatGPT or an authenticated smoke call requires reauthorization. Reuse user-managed HTTPS routes unchanged.
- MCP OAuth returns a persistent workspace Bearer stored in private per-user state; keep it separate from the local control credential and never display it.
- Persist OAuth dynamic-client registrations and the workspace Bearer in private per-user state. Persistent-Bearer authorization validates the exact registered HTTPS ChatGPT Connector callback, workspace resource/scope, and PKCE S256, then completes directly; do not add a pairing-code or browser-enrollment flow. Never display the Bearer.
- Keep exact redirect URI, workspace resource, scope, and PKCE S256 checks. Accept `https://chatgpt.com` on Connector OAuth endpoints and `Origin: null` only on `/authorize`; keep exact Host validation.
- MCP read access uses the persistent workspace Bearer. Every write/validation mutation also requires a one-task `task_capability` issued by authenticated local control. Store only its hash; never put the raw value in workspace files, packets, reports, journals, or logs. Separate leases by session and task, and never expose active task authority through `workspace_info`.
- Prefer a Cloudflare Named Tunnel when the user has a Cloudflare-managed domain; use Quick Tunnel only when they do not.
