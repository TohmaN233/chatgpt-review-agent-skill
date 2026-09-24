# MCP setup

Install the Skills separately. Installation ends without downloading or starting
the Bridge. `$chatgpt-agent-setup` uses ZIP by default and runs this procedure
only when you choose MCP.

For MCP, the setup Skill installs missing Python, Git and `cloudflared`, obtains
the Bridge package, then configures one Bridge and Connector for the current
workspace. A Cloudflare-managed domain is the recommended route for a fixed
Named Tunnel. Use a Quick Tunnel only when you do not have such a domain. Quick
Tunnel addresses can change on restart; the setup Skill then updates only this
workspace's Connector.

The Bridge remains running until stopped or the computer shuts down. Connector
sessions start read-only. Review and plan can save task reports; local source
writes require the selected task's scoped grant. GitHub authorization is
separate.

Setup completes after the Connector is authorized, `workspace_info` succeeds
in ChatGPT for the configured workspace, and this command reports `ready: true`:

```bash
python <repository>/agentctl.py doctor --workspace <workspace> --json
```

Stop a workspace connection with:

```bash
python <repository>/agentctl.py stop --workspace <workspace> --json
```
