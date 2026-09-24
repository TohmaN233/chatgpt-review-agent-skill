#!/usr/bin/env python3
"""Deterministic local control plane for the ChatGPT Agent MCP bridge.

This CLI manages the local bridge. The setup Skill handles the ChatGPT UI and
consumes a server-issued tool-call receipt with ``mark-verified``. ``doctor`` only
returns READY when the local bridge, public endpoint (when configured), OAuth
metadata, and the persisted real-tool smoke result all pass.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mcp_server import PROFILE_CAPABILITIES, load_or_create_token, workspace_id
from bridge_security import https_base
from host_control import atomic_json, writer_lock, request_json


STATE_VERSION = 4


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def state_home() -> Path:
    explicit = os.environ.get("CHATGPT_AGENT_HOME")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.name == "nt":
        base = Path(
            os.environ.get(
                "LOCALAPPDATA", Path.home() / "AppData" / "Local"
            )
        )
        return base / "chatgpt-agent"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "chatgpt-agent"
        )
    return (
        Path(
            os.environ.get(
                "XDG_STATE_HOME", Path.home() / ".local" / "state"
            )
        )
        / "chatgpt-agent"
    )


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def workspace_paths(root: Path) -> dict[str, Path]:
    wid = workspace_id(root)
    home = state_home()
    if home.is_relative_to(root.resolve()):
        raise ValueError("CHATGPT_AGENT_HOME must be outside the exposed workspace")
    ensure_private_dir(home)
    return {
        "home": home,
        "state": ensure_private_dir(home / "workspaces") / f"{wid}.json",
        "token": ensure_private_dir(home / "tokens") / f"{wid}.token",
        "mcp_token": ensure_private_dir(home / "tokens") / f"{wid}.mcp-token",
        "log": ensure_private_dir(home / "logs") / f"{wid}.log",
        "journal": ensure_private_dir(home / "operations") / wid,
        "lock": ensure_private_dir(home / "locks") / f"{wid}.lock",
    }


def load_state(root: Path) -> tuple[dict[str, Any] | None, dict[str, Path]]:
    paths = workspace_paths(root)
    if not paths["state"].exists():
        return None, paths
    return (
        json.loads(paths["state"].read_text(encoding="utf-8")),
        paths,
    )


def save_state(root: Path, state: dict[str, Any]) -> dict[str, Path]:
    paths = workspace_paths(root)
    state["updated_at"] = utc_now()
    atomic_json(paths["state"], state)
    return paths


def choose_port(host: str, requested: int) -> int:
    if requested:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, requested))
                return requested
            except OSError:
                pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])





def fetch_json(url: str, timeout: float = 3.0) -> tuple[bool, Any]:
    try:
        return True, request_json(url, timeout=timeout, headers={
            "Accept": "application/json",
            "User-Agent": "ChatGPT-Agent-Doctor/1.0",
        })
    except urllib.error.HTTPError as exc:
        return False, {"error": "http_error", "status": exc.code}
    except urllib.error.URLError as exc:
        return False, {"error": "url_error", "reason_type": type(exc.reason).__name__}
    except ValueError:
        return False, {"error": "invalid_json"}
    except OSError as exc:
        return False, {"error": type(exc).__name__}


def control(root: Path, operation: str, **payload) -> dict:
    state, paths = load_state(root)
    if state is None or state["host"] not in {"127.0.0.1", "localhost"}:
        raise ValueError("workspace is not configured for loopback control")
    credential = load_or_create_token(paths["token"], None)
    return request_json(f"http://127.0.0.1:{state['port']}/control",
                        body={"operation": operation, **payload},
                        headers={"X-ChatGPT-Agent-Admin": credential}, local=True)


def matches(state: dict, ping: dict) -> bool:
    endpoint = ping.get("endpoint")
    if state.get("tunnel_mode") == "quick":
        try:
            parsed = urlsplit(endpoint)
            endpoint_ok = (parsed.scheme == "https" and parsed.path == "/mcp"
                           and not parsed.query and not parsed.fragment and parsed.hostname
                           and parsed.port is None
                           and parsed.hostname.lower().endswith(".trycloudflare.com")
                           and parsed.hostname.lower() != "api.trycloudflare.com"
                           and parsed.username is None and parsed.password is None)
        except (TypeError, ValueError):
            endpoint_ok = False
    else:
        base = state.get("public_url") or f"http://127.0.0.1:{state['port']}"
        endpoint_ok = endpoint == base + "/mcp"
    return (ping.get("workspace_id") == state["workspace_id"]
            and ping.get("profile") == state["profile"] and endpoint_ok
            and (state.get("tunnel_mode") not in {"quick", "named"} or ping.get("tunnel_ready") is True)
            and isinstance(ping.get("boot_id"), str))


def public_health(state: dict, *, attempts: int = 3) -> bool:
    public_url = state.get("public_url")
    if not public_url:
        return False
    for attempt in range(attempts):
        ok, payload = fetch_json(public_url + "/health", timeout=3.0)
        if (ok and isinstance(payload, dict)
                and payload.get("service") == "chatgpt-agent-mcp"
                and payload.get("workspace_id") == state.get("workspace_id")):
            return True
        if attempt + 1 < attempts:
            time.sleep(.5)
    return False


def connector_action_for(state: dict) -> str:
    """Describe the workspace Connector action required for its current URL."""
    if not state.get("connector_name"):
        return "create"
    public_url = state.get("public_url")
    if public_url and state.get("connector_endpoint") != public_url:
        return "update"
    return "none"


def sync_quick_tunnel_url(state: dict, ping: dict) -> bool:
    """Persist a new Quick Tunnel URL observed from the running Bridge."""
    if state.get("tunnel_mode") != "quick":
        return False
    endpoint = ping.get("endpoint")
    try:
        parsed = urlsplit(endpoint)
        hostname = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or parsed.path != "/mcp"
                or parsed.query or parsed.fragment or parsed.username
                or parsed.password or parsed.port is not None
                or not hostname.endswith(".trycloudflare.com")
                or hostname == "api.trycloudflare.com"):
            raise ValueError("invalid Quick Tunnel endpoint")
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid Quick Tunnel endpoint") from exc
    current_url = "https://" + parsed.netloc
    if state.get("public_url") == current_url:
        return False
    state["public_url"] = current_url
    clear_verification(state)
    return True


def diagnostic_for(exc: BaseException) -> dict[str, Any]:
    """Return safe, actionable error metadata without exposing request secrets."""
    if isinstance(exc, urllib.error.HTTPError):
        return {"error": "http_error", "status": exc.code}
    if isinstance(exc, urllib.error.URLError):
        return {"error": "url_error", "reason_type": type(exc.reason).__name__}
    if isinstance(exc, OSError):
        return {"error": type(exc).__name__, "errno": exc.errno}
    if isinstance(exc, ValueError):
        return {"error": "invalid_response", "detail": str(exc)[:160]}
    return {"error": type(exc).__name__}


def doctor_action(checks: dict[str, str], state: dict) -> str:
    if checks.get("state") != "pass":
        return "Run agentctl setup for this workspace."
    if checks.get("process") != "pass":
        return "Run agentctl start; inspect the workspace Bridge log if its startup probe fails."
    if checks.get("local_mcp") != "pass":
        return "Restart the workspace Bridge and inspect the local_mcp diagnostic before retrying."
    if checks.get("public_endpoint") == "not_configured":
        return "Configure a public HTTPS route for MCP, then restart the workspace Bridge."
    if checks.get("public_endpoint") != "pass" or checks.get("oauth") != "pass":
        return "Repair the public route or OAuth metadata using the reported diagnostics; reconnect only if ChatGPT reports an authorization error."
    action = connector_action_for(state)
    if action == "create":
        return "Create the ChatGPT Connector for this workspace, authorize it, then call workspace_info."
    if action == "update":
        return "Update this workspace's ChatGPT Connector to the current public URL, then call workspace_info."
    if checks.get("tool_smoke") != "pass":
        return "Call workspace_info through the existing Connector and record the returned smoke receipt."
    return "none"


def clear_verification(state: dict) -> None:
    for field in ("tool_verified_at", "tool_verified_workspace_id", "receipt_id", "verified_boot_id"):
        state[field] = None


def bridge_script() -> Path:
    return Path(__file__).resolve().with_name("mcp_server.py")


def bridge_fingerprint() -> str:
    directory = Path(__file__).resolve().parent
    modules = ("agentctl.py", "mcp_server.py", "bridge_http.py", "bridge_runtime.py",
               "bridge_security.py", "bridge_policy.py", "cloudflare_tunnel.py", "host_control.py")
    digest = hashlib.sha256()
    for name in modules:
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(f"Bridge package is incomplete: {name} is missing")
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def emit(value: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    for key, val in value.items():
        if isinstance(val, dict):
            print(f"{key}:")
            for child, child_value in val.items():
                print(f"  {child}: {child_value}")
        else:
            print(f"{key}: {val}")


def setup_command(args: argparse.Namespace) -> int:
    root = Path(args.workspace).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"workspace does not exist: {root}")
    state, paths = load_state(root)
    previous = state or {}
    profile = args.profile or previous.get("profile") or "review"
    host = args.host or previous.get("host") or "127.0.0.1"
    requested_tunnel_mode = getattr(args, "tunnel_mode", None)
    tunnel_mode = (requested_tunnel_mode if requested_tunnel_mode is not None
                   else previous.get("tunnel_mode", "none"))
    tunnel_name = getattr(args, "tunnel_name", None) or (previous.get("tunnel_name") if tunnel_mode == "named" else None)
    tunnel_id = previous.get("tunnel_id") if tunnel_mode == "named" else None
    hostname = getattr(args, "hostname", None) or (previous.get("hostname") if tunnel_mode == "named" else None)
    if args.port is not None and not 0 <= args.port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("bridge must bind to loopback")
    if tunnel_mode == "quick" and args.public_url:
        raise ValueError("Quick Tunnel chooses its own public address")
    if args.public_url:
        args.public_url = https_base(args.public_url)
    if tunnel_mode == "named":
        from cloudflare_tunnel import named_state_path
        named_path = named_state_path(paths["home"], workspace_id(root))
        if named_path.is_file():
            named = json.loads(named_path.read_text(encoding="utf-8"))
            if named.get("workspace_id") != workspace_id(root):
                raise ValueError("named tunnel state belongs to another workspace")
            tunnel_name = tunnel_name or named.get("tunnel_name")
            tunnel_id = tunnel_id or named.get("tunnel_id")
            hostname = hostname or named.get("hostname")
        if not tunnel_name or not tunnel_id or not hostname:
            raise ValueError("run tunnel-provision for this workspace before selecting named mode")
        public_url = https_base("https://" + hostname)
    else:
        public_url = args.public_url
    if state is not None and args.port is None:
        port = int(previous.get("port") or 8765)
    else:
        requested_port = args.port if args.port is not None else 8765
        port = choose_port(host, requested_port)
    if state is None:
        state = {
            "version": STATE_VERSION,
            "workspace_id": workspace_id(root),
            "workspace_name": root.name,
            "root": str(root),
            "profile": profile,
            "host": host,
            "port": port,
            "public_url": public_url,
            "tunnel_mode": tunnel_mode,
            "tunnel_name": tunnel_name,
            "tunnel_id": tunnel_id,
            "hostname": hostname,
            "connector_name": None,
            "connector_endpoint": None,
            "tool_verified_at": None,
            "server_pid": None,
            "created_at": utc_now(),
        }
    else:
        changed = (state.get("profile") != profile or state.get("host") != host or state.get("port") != port
                   or state.get("tunnel_mode", "none") != tunnel_mode
                   or (tunnel_mode == "named" and (state.get("hostname") != hostname
                                                     or state.get("tunnel_name") != tunnel_name
                                                     or state.get("tunnel_id") != tunnel_id))
                   or (public_url is not None and state.get("public_url") != public_url)
                   or (tunnel_mode == "none" and previous.get("tunnel_mode") in {"quick", "named"}))
        if changed:
            stop_command(argparse.Namespace(workspace=str(root), json=True, quiet=True))
            clear_verification(state)
            state["server_pid"] = None
        state.update({"version": STATE_VERSION, "profile": profile, "host": host, "port": port,
                      "tunnel_mode": tunnel_mode, "tunnel_name": tunnel_name,
                      "tunnel_id": tunnel_id, "hostname": hostname})
        if not state.get("connector_endpoint") and state.get("connector_name"):
            state["connector_endpoint"] = previous.get("public_url")
        if public_url is not None:
            state["public_url"] = public_url
        elif tunnel_mode == "none" and previous.get("tunnel_mode") in {"quick", "named"}:
            state["public_url"] = None
    load_or_create_token(paths["token"], None)
    load_or_create_token(paths["mcp_token"], None)
    save_state(root, state)
    emit(
        {
            "ok": True,
            "workspace_id": state["workspace_id"],
            "workspace": str(root),
            "profile": state["profile"],
            "tunnel_mode": state.get("tunnel_mode", "none"),
            "host": state["host"],
            "port": state["port"],
            "state_file": str(paths["state"]),
            "next": (
                "run agentctl.py start, then configure the ChatGPT connector"
            ),
        },
        args.json,
    )
    return 0


def tunnel_provision_command(args: argparse.Namespace) -> int:
    root = Path(args.workspace).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"workspace does not exist: {root}")
    paths = workspace_paths(root)
    from cloudflare_tunnel import provision_named_tunnel
    tunnel = provision_named_tunnel(
        home=paths["home"],
        workspace_id=workspace_id(root),
        domain=args.domain,
        hostname=args.hostname,
    )
    emit({"ok": True, "workspace_id": workspace_id(root), "provider": "cloudflare-named",
          **tunnel, "public_url": "https://" + tunnel["hostname"]}, args.json)
    return 0


def start_command(args: argparse.Namespace) -> int:
    root = Path(args.workspace).expanduser().resolve()
    state, paths = load_state(root)
    if state is None:
        raise ValueError("workspace is not configured; run setup first")
    try:
        ping = control(root, "ping")
    except (OSError, ValueError):
        ping = None
    if ping:
        if not matches(state, ping):
            raise ValueError("running bridge identity/configuration mismatch; stop it before restart")
        if state.get("package_fingerprint") != bridge_fingerprint():
            stop_command(argparse.Namespace(workspace=str(root), json=True, quiet=True))
            state, paths = load_state(root)
        elif state.get("tunnel_mode") in {"quick", "named"} and not public_health(state):
            stop_command(argparse.Namespace(workspace=str(root), json=True, quiet=True))
            state, paths = load_state(root)
        else:
            if sync_quick_tunnel_url(state, ping):
                save_state(root, state)
            if not getattr(args, "quiet", False):
                emit({"ok": True, "reused": True, "health": ping,
                      "public_url": state.get("public_url"),
                      "connector_action": connector_action_for(state)}, args.json)
            return 0
    with socket.socket() as sock:
        sock.settimeout(.5)
        if sock.connect_ex(("127.0.0.1", state["port"])) == 0:
            raise ValueError("port is occupied by an unverified service; refusing to reuse or kill it")
    command = [sys.executable, str(bridge_script()), "--root", str(root), "--host", state["host"],
               "--port", str(state["port"]), "--token-file", str(paths["token"]),
               "--mcp-token-file", str(paths["mcp_token"]),
               "--oauth-client-registry-file",
               str(paths["mcp_token"].with_name(paths["mcp_token"].name + ".clients.json")),
               "--state-dir", str(paths["journal"]), "--profile", state["profile"]]
    tunnel_mode = state.get("tunnel_mode", "none")
    if tunnel_mode in {"quick", "named"}:
        command += ["--tunnel-mode", tunnel_mode,
                    "--tunnel-log", str(paths["home"] / "logs" / f"{state['workspace_id']}-tunnel.log")]
    if tunnel_mode == "named":
        command += ["--tunnel-name", state["tunnel_name"], "--hostname", state["hostname"],
                    "--tunnel-id", state["tunnel_id"], "--public-url", state["public_url"]]
    elif state.get("public_url") and tunnel_mode != "quick":
        command += ["--public-url", state["public_url"]]
    with paths["log"].open("a", encoding="utf-8") as log:
        kwargs = {"cwd": str(root), "stdout": log, "stderr": subprocess.STDOUT,
                  "stdin": subprocess.DEVNULL, "close_fds": True}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **kwargs)
    deadline = time.monotonic() + (100 if tunnel_mode in {"quick", "named"} else 8)
    last_probe = "no authenticated response"
    while time.monotonic() < deadline and process.poll() is None:
        try:
            ping = control(root, "ping")
            if matches(state, ping):
                sync_quick_tunnel_url(state, ping)
                state["server_pid"] = process.pid
                state["server_started_at"] = utc_now()
                state["boot_id"] = ping["boot_id"]
                state["package_fingerprint"] = bridge_fingerprint()
                clear_verification(state)
                save_state(root, state)
                connector_action = connector_action_for(state)
                if not getattr(args, "quiet", False):
                    emit({"ok": True, "reused": False, "pid": process.pid, "health": ping,
                          "local_mcp": f"http://127.0.0.1:{state['port']}/mcp",
                          "public_mcp": ping["endpoint"] if state.get("public_url") else None,
                          "public_url": state.get("public_url"),
                          "connector_name": state.get("connector_name"),
                          "connector_action": connector_action}, args.json)
                return 0
            last_probe = "workspace/profile/endpoint identity mismatch"
        except (OSError, ValueError) as exc:
            last_probe = type(exc).__name__ + ": " + str(exc)
        time.sleep(.1)
    if process.poll() is None:
        process.terminate()  # Only the child we just launched, never a persisted PID.
        process.wait(timeout=5)
    from bridge_runtime import sanitize
    detail = sanitize(last_probe, [root, paths["home"]])[:500]
    raise ValueError("bridge failed its authenticated startup probe (" + detail + "); inspect the local log")





def stop_command(args: argparse.Namespace) -> int:
    root = Path(args.workspace).expanduser().resolve()
    state, _ = load_state(root)
    if state is None:
        if not getattr(args, "quiet", False):
            emit({"ok": True, "stopped": False}, args.json)
        return 0
    stopped = False
    try:
        ping = control(root, "ping")
        if ping.get("workspace_id") != state["workspace_id"]:
            raise ValueError("refusing to stop a different workspace")
        control(root, "stop")
        stopped = True
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                control(root, "ping")
            except (OSError, ValueError):
                break
            time.sleep(.1)
        else:
            raise ValueError("bridge did not acknowledge shutdown completion")
    except OSError:
        # Unreachable/dead process: a stale PID is not authority to terminate it.
        with socket.socket() as sock:
            sock.settimeout(.5)
            if sock.connect_ex(("127.0.0.1", state["port"])) == 0:
                raise ValueError("service is reachable but unverified; refusing PID-based termination")
    state["server_pid"] = None
    clear_verification(state)
    save_state(root, state)
    if not getattr(args, "quiet", False):
        emit({"ok": True, "stopped": stopped}, args.json)
    return 0


def status_report(root: Path) -> dict[str, Any]:
    state, paths = load_state(root)
    if state is None:
        return {"configured": False}
    try:
        ping = control(root, "ping")
        alive = matches(state, ping)
    except (OSError, ValueError):
        ping, alive = None, False
    return {"configured": True, "workspace_id": state["workspace_id"], "profile": state["profile"],
            "tunnel_mode": state.get("tunnel_mode", "none"),
            "process_alive": alive, "health_detail": ping, "public_url": state.get("public_url"),
            "connector_name": state.get("connector_name"),
            "connector_endpoint": state.get("connector_endpoint"), "receipt_id": state.get("receipt_id"),
            "state_file": str(paths["state"])}


def status_command(args: argparse.Namespace) -> int:
    root = Path(args.workspace).expanduser().resolve()
    emit(status_report(root), args.json)
    return 0


def doctor_report(root: Path) -> dict[str, Any]:
    state, _ = load_state(root)
    if state is None:
        return {"ready": False, "checks": {"state": "fail"}, "diagnostics": {},
                "action": "run setup"}
    checks = {"state": "pass", "process": "fail", "local_mcp": "fail", "oauth": "fail",
              "public_endpoint": "not_configured", "connector": "unknown", "tool_smoke": "unknown"}
    diagnostics: dict[str, Any] = {}
    ping, receipt = {}, None
    base = f"http://127.0.0.1:{state['port']}"
    try:
        ping = control(root, "ping")
        checks["process"] = "pass" if matches(state, ping) else "fail"
    except (OSError, ValueError) as exc:
        diagnostics["process"] = diagnostic_for(exc)
    if ping and checks["process"] != "pass":
        diagnostics["process"] = {"error": "bridge_identity_mismatch"}

    try:
        probe = control(root, "probe")
        headers = {"Authorization": "Bearer " + probe["access_token"]}
        initialized = request_json(base + "/mcp", body={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                                   headers=headers, local=True)
        info = request_json(base + "/mcp", body={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": "workspace_info", "arguments": {}}}, headers=headers, local=True)
        data = info.get("result", {}).get("structuredContent", {})
        checks["local_mcp"] = "pass" if (initialized.get("result", {}).get("serverInfo", {}).get("name") == "chatgpt-agent-mcp"
                            and data.get("workspace_id") == state["workspace_id"]
                            and data.get("boot_id") == ping.get("boot_id")) else "fail"
        if data.get("session_id"):
            control(root, "disconnect", session_id=data["session_id"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        diagnostics["local_mcp"] = diagnostic_for(exc)

    expected = state.get("public_url") or base
    try:
        ok, metadata = fetch_json(base + "/.well-known/oauth-authorization-server")
        oauth_valid = (ok and isinstance(metadata, dict)
                       and metadata.get("issuer") == expected
                       and metadata.get("token_endpoint") == expected + "/token"
                       and "S256" in metadata.get("code_challenge_methods_supported", []))
        checks["oauth"] = "pass" if oauth_valid else "fail"
        if not ok or not oauth_valid:
            diagnostics["oauth"] = metadata if isinstance(metadata, dict) else {"error": "invalid_metadata"}
            if ok:
                diagnostics["oauth"] = {"error": "metadata_mismatch"}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        diagnostics["oauth"] = diagnostic_for(exc)

    if state.get("public_url"):
        try:
            ok, public = fetch_json(state["public_url"] + "/health", timeout=5)
            public_valid = (ok and isinstance(public, dict) and matches(state, public)
                            and public.get("boot_id") == ping.get("boot_id"))
            checks["public_endpoint"] = "pass" if public_valid else "fail"
            if not ok:
                diagnostics["public_endpoint"] = public if isinstance(public, dict) else {"error": "invalid_health_response"}
            elif not public_valid:
                diagnostics["public_endpoint"] = {"error": "health_identity_mismatch"}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            diagnostics["public_endpoint"] = diagnostic_for(exc)

    if state.get("receipt_id"):
        try:
            receipt = control(root, "receipt-status", receipt_id=state["receipt_id"]).get("receipt")
            valid = bool(receipt and receipt["workspace_id"] == state["workspace_id"]
                         and receipt["boot_id"] == ping.get("boot_id")
                         and receipt["endpoint"] == expected + "/mcp"
                         and receipt["connector_name"] == state.get("connector_name"))
            checks["tool_smoke"] = "pass" if valid else "fail"
            checks["connector"] = "pass" if valid else "unknown"
            if not valid:
                diagnostics["tool_smoke"] = {"error": "receipt_does_not_match_current_workspace_route"}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            diagnostics["tool_smoke"] = diagnostic_for(exc)
    ready = all(value == "pass" for value in checks.values())
    return {"ready": ready, "local_ready": all(checks[key] == "pass" for key in ("process", "local_mcp", "oauth")),
            "workspace_id": state["workspace_id"], "checks": checks, "diagnostics": diagnostics,
            "receipt": receipt,
            "action": doctor_action(checks, state),
            "provenance_note": "A receipt proves a connector call, not the client's brand. Observe the ChatGPT UI separately."}


def doctor_command(args: argparse.Namespace) -> int:
    root = Path(args.workspace).expanduser().resolve()
    report = doctor_report(root)
    emit(report, args.json)
    return 0 if report["ready"] else 2


def endpoint_command(args: argparse.Namespace) -> int:
    root = Path(args.workspace).expanduser().resolve()
    state, _paths = load_state(root)
    if state is None:
        raise SystemExit("workspace is not configured")
    value = https_base(args.public_url) if args.public_url else None
    stop_command(argparse.Namespace(workspace=str(root), json=True, quiet=True))
    state["server_pid"] = None
    clear_verification(state)
    state["public_url"] = value
    state.update({"tunnel_mode": "none", "tunnel_name": None, "tunnel_id": None, "hostname": None})
    state["tool_verified_at"] = None
    save_state(root, state)
    emit(
        {
            "ok": True,
            "public_url": state["public_url"],
            "restart_required": True,
            "connector_reverification_required": True,
        },
        args.json,
    )
    return 0


def profile_command(args: argparse.Namespace) -> int:
    root = Path(args.workspace).expanduser().resolve()
    state, _paths = load_state(root)
    if state is None:
        raise SystemExit("workspace is not configured")
    old = state.get("profile")
    stop_command(argparse.Namespace(workspace=str(root), json=True, quiet=True))
    clear_verification(state)
    state["server_pid"] = None
    state["profile"] = args.profile
    state["tool_verified_at"] = None
    save_state(root, state)
    if args.restart:
        stop_command(
            argparse.Namespace(workspace=str(root), json=True, quiet=True)
        )
        start_command(
            argparse.Namespace(workspace=str(root), json=True, quiet=True)
        )
    emit(
        {
            "ok": True,
            "old_profile": old,
            "profile": args.profile,
            "capabilities": sorted(PROFILE_CAPABILITIES[args.profile]),
            "restart_required": not args.restart,
        },
        args.json,
    )
    return 0


def verified_command(args: argparse.Namespace) -> int:
    root = Path(args.workspace).expanduser().resolve()
    state, _ = load_state(root)
    if state is None:
        raise ValueError("workspace is not configured")
    receipt = control(root, "mark-verified", receipt_id=args.receipt_id, connector_name=args.connector_name)
    state["connector_name"] = args.connector_name
    state["connector_endpoint"] = state.get("public_url")
    state["receipt_id"] = receipt["receipt_id"]
    state["verified_boot_id"] = receipt["boot_id"]
    state["tool_verified_at"] = dt.datetime.fromtimestamp(receipt["observed_at"], dt.timezone.utc).isoformat()
    save_state(root, state)
    emit({"ok": True, "receipt": receipt}, args.json)
    return 0


def control_command(args: argparse.Namespace) -> int:
    root = Path(args.workspace).expanduser().resolve()
    payload = {key: getattr(args, key) for key in ("session_id", "task_id", "profile", "ttl",
                                                    "connector_name", "client_id", "redirect_uri",
                                                    "client_fingerprint")
               if getattr(args, key, None) is not None}
    if args.command == "grant-task":
        payload.update(paths=args.path, validations=args.validation)
    emit(control(root, args.command, **payload), args.json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentctl")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser(
        "setup", help="Initialize deterministic local bridge state"
    )
    setup.add_argument("--workspace", default=".")
    setup.add_argument(
        "--profile", choices=sorted(PROFILE_CAPABILITIES)
    )
    setup.add_argument("--host")
    setup.add_argument(
        "--port",
        type=int,
        help="Use 0 to choose a free port; default is 8765",
    )
    setup.add_argument("--public-url")
    setup.add_argument("--tunnel-mode", choices=("none", "quick", "named"))
    setup.add_argument("--tunnel-name")
    setup.add_argument("--hostname")
    setup.add_argument("--json", action="store_true")
    setup.set_defaults(func=setup_command)

    tunnel = sub.add_parser("tunnel-provision", help="Create this workspace's Cloudflare Named Tunnel")
    tunnel.add_argument("--workspace", default=".")
    tunnel.add_argument("--domain", required=True, help="Cloudflare-managed DNS zone")
    tunnel.add_argument("--hostname", help="Optional subdomain; defaults to a workspace-scoped name")
    tunnel.add_argument("--json", action="store_true")
    tunnel.set_defaults(func=tunnel_provision_command)

    for name, func in (
        ("start", start_command),
        ("stop", stop_command),
        ("status", status_command),
        ("doctor", doctor_command),
    ):
        command = sub.add_parser(name)
        command.add_argument("--workspace", default=".")
        command.add_argument("--json", action="store_true")
        command.set_defaults(func=func)

    endpoint = sub.add_parser("set-endpoint")
    endpoint.add_argument("--workspace", default=".")
    endpoint.add_argument("--public-url", required=True)
    endpoint.add_argument("--json", action="store_true")
    endpoint.set_defaults(func=endpoint_command)

    profile = sub.add_parser("profile")
    profile.add_argument("--workspace", default=".")
    profile.add_argument("profile", choices=sorted(PROFILE_CAPABILITIES))
    profile.add_argument("--restart", action="store_true")
    profile.add_argument("--json", action="store_true")
    profile.set_defaults(func=profile_command)

    verified = sub.add_parser("mark-verified")
    verified.add_argument("--workspace", default=".")
    verified.add_argument("--connector-name", required=True)
    verified.add_argument("--receipt-id", required=True)
    verified.add_argument("--json", action="store_true")
    verified.set_defaults(func=verified_command)
    for name in ("pair", "sessions", "grant-task",
                 "revoke-task", "disconnect", "begin-smoke", "checkpoint",
                 "connector-clients", "approve-client"):
        command = sub.add_parser(name)
        command.add_argument("--workspace", default=".")
        command.add_argument("--json", action="store_true")
        if name in {"grant-task", "revoke-task", "disconnect"}:
            command.add_argument("--session-id", required=True)
        if name == "grant-task":
            command.add_argument("--task-id")
        if name in {"revoke-task", "checkpoint"}:
            command.add_argument("--task-id", required=True)
        if name == "grant-task":
            command.add_argument("--profile", choices=sorted(PROFILE_CAPABILITIES), default="review")
            command.add_argument("--path", action="append", default=[])
            command.add_argument("--validation", action="append", default=[])
            command.add_argument("--ttl", type=int, default=900)
        if name == "begin-smoke":
            command.add_argument("--connector-name", required=True)
        if name == "approve-client":
            command.add_argument("--client-fingerprint", required=True)
        command.set_defaults(func=control_command)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command in {"setup", "tunnel-provision", "start", "stop", "set-endpoint", "profile", "mark-verified", "approve-client"}:
            root = Path(args.workspace).expanduser().resolve()
            with writer_lock(workspace_paths(root)["lock"]):
                return int(args.func(args))
        return int(args.func(args))
    except (OSError, ValueError, RuntimeError) as exc:
        print("Control operation failed: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
