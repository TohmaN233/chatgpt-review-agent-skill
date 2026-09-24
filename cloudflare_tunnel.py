"""Workspace-scoped Cloudflare Tunnel setup and process supervision."""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from host_control import atomic_json


QUICK_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)
TUNNEL_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)
REGISTERED = re.compile(r"registered tunnel connection", re.IGNORECASE)


def binary() -> str:
    path = shutil.which("cloudflared")
    if path:
        return path
    candidates = [
        Path("/opt/homebrew/bin/cloudflared"),
        Path("/usr/local/bin/cloudflared"),
        Path("/usr/bin/cloudflared"),
    ]
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe",
            Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "cloudflared" / "cloudflared.exe",
            Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")) / "cloudflared" / "cloudflared.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "cloudflared" / "cloudflared.exe",
        ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError("cloudflared is not installed; run the MCP setup dependency step")


def normalize_domain(value: str) -> str:
    raw = value.strip().rstrip(".")
    if not raw or "://" in raw or "/" in raw or "@" in raw:
        raise ValueError("enter a Cloudflare-managed domain such as example.com")
    try:
        domain = raw.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("invalid domain name") from exc
    labels = domain.split(".")
    if (len(domain) > 253 or len(labels) < 2
            or any(not HOST_LABEL.fullmatch(label) for label in labels)):
        raise ValueError("invalid domain name")
    return domain


def normalize_hostname(value: str) -> str:
    domain = normalize_domain(value)
    if len(domain) > 253:
        raise ValueError("hostname is too long")
    return domain


def named_state_path(home: Path, workspace_id: str) -> Path:
    return home / "tunnels" / f"{workspace_id}.json"


def _run_cloudflared(args: list[str], *, timeout: int = 45) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            [binary(), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"cloudflared command timed out after {timeout} seconds") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1200:]
        raise RuntimeError(detail or f"cloudflared exited with status {result.returncode}")
    return result


def provision_named_tunnel(
    *, home: Path, workspace_id: str, domain: str, hostname: str | None = None,
) -> dict[str, str]:
    """Create or reuse only this workspace's named tunnel and DNS route."""
    zone = normalize_domain(domain)
    path = named_state_path(home, workspace_id)
    if path.is_file():
        current = json.loads(path.read_text(encoding="utf-8"))
        if (current.get("workspace_id") == workspace_id
                and current.get("provider") == "cloudflare-named"):
            if current.get("zone") != zone:
                raise ValueError("this workspace already has a named tunnel in another domain")
            return {key: str(current[key]) for key in ("tunnel_name", "tunnel_id", "hostname", "zone")}

    tunnel_name = f"chatgpt-agent-{workspace_id.removeprefix('ws_')[:12]}"
    routed_host = normalize_hostname(hostname) if hostname else f"cga-{workspace_id[-12:]}.{zone}"
    if routed_host == zone or not routed_host.endswith("." + zone):
        raise ValueError("the tunnel hostname must be a subdomain of the selected Cloudflare domain")

    cert = Path.home() / ".cloudflared" / "cert.pem"
    if not cert.is_file():
        result = subprocess.run(
            [binary(), "tunnel", "login"],
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode or not cert.is_file():
            raise RuntimeError("Cloudflare login did not finish; complete the browser authorization and retry")

    listed = _run_cloudflared(["tunnel", "list", "--output", "json"])
    try:
        tunnels = json.loads(listed.stdout or listed.stderr)
    except json.JSONDecodeError as exc:
        raise RuntimeError("cloudflared returned an unreadable tunnel list") from exc
    if isinstance(tunnels, dict):
        tunnels = tunnels.get("tunnels", [])
    if not isinstance(tunnels, list):
        raise RuntimeError("cloudflared returned an invalid tunnel list")
    existing = next((item for item in tunnels if isinstance(item, dict) and item.get("name") == tunnel_name), None)
    if existing is None:
        created = _run_cloudflared(["tunnel", "create", tunnel_name])
        match = TUNNEL_ID.search(created.stdout + "\n" + created.stderr)
        if not match:
            raise RuntimeError("cloudflared created a tunnel but did not return its identity")
        tunnel_id = match.group(0).lower()
    else:
        tunnel_id = str(existing.get("id", "")).lower()
        if not TUNNEL_ID.fullmatch(tunnel_id):
            raise RuntimeError("cloudflared returned an invalid tunnel identity")

    _run_cloudflared(["tunnel", "route", "dns", tunnel_name, routed_host])
    value = {
        "version": "1",
        "workspace_id": workspace_id,
        "provider": "cloudflare-named",
        "tunnel_name": tunnel_name,
        "tunnel_id": tunnel_id,
        "hostname": routed_host,
        "zone": zone,
    }
    atomic_json(path, value)
    return {key: value[key] for key in ("tunnel_name", "tunnel_id", "hostname", "zone")}


class CloudflareTunnel:
    """Own the cloudflared child process for exactly one Bridge lifetime."""

    def __init__(self, *, mode: str, log_path: Path, tunnel_name: str | None = None,
                 tunnel_id: str | None = None):
        if mode not in {"quick", "named"}:
            raise ValueError("unsupported Cloudflare tunnel mode")
        if mode == "named" and (not tunnel_name or not tunnel_id or not TUNNEL_ID.fullmatch(tunnel_id)):
            raise ValueError("named tunnel identity is incomplete or invalid")
        self.mode = mode
        self.log_path = log_path
        self.tunnel_name = tunnel_name
        self.tunnel_id = tunnel_id
        self.process: subprocess.Popen[str] | None = None
        self.reader: threading.Thread | None = None
        self.config_path: Path | None = None

    def _command(self, local_port: int) -> list[str]:
        command = [binary(), "tunnel", "--no-autoupdate", "--protocol", "http2"]
        if self.mode == "named":
            credentials = Path.home() / ".cloudflared" / f"{self.tunnel_id}.json"
            if not credentials.is_file():
                raise RuntimeError(f"credentials for named tunnel {self.tunnel_name} are missing: {credentials}")
            self.config_path = self.log_path.with_name(f"{self.tunnel_name}.config.yml")
            config = (
                f"tunnel: {self.tunnel_id}\n"
                f"credentials-file: {json.dumps(str(credentials))}\n"
                "ingress:\n"
                f"  - service: http://127.0.0.1:{local_port}\n"
                "  - service: http_status:404\n"
            )
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(config, encoding="utf-8")
            command += ["--config", str(self.config_path), "run", str(self.tunnel_name)]
        else:
            command += ["--url", f"http://127.0.0.1:{local_port}"]
        return command

    def start(self, local_port: int, *, timeout: float = 45.0) -> str:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.process = subprocess.Popen(
                self._command(local_port),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError as exc:
            self.stop()
            raise RuntimeError(f"could not start cloudflared: {exc}") from exc
        except BaseException:
            self.stop()
            raise

        lines: queue.Queue[str | BaseException | None] = queue.Queue()

        process = self.process

        def collect() -> None:
            assert process and process.stdout
            try:
                with self.log_path.open("a", encoding="utf-8") as log:
                    for line in process.stdout:
                        log.write(line)
                        log.flush()
                        lines.put(line)
            except OSError as exc:
                lines.put(exc)
                return
            lines.put(None)

        self.reader = threading.Thread(target=collect, name="cloudflared-log", daemon=True)
        self.reader.start()
        deadline = time.monotonic() + timeout
        tail: list[str] = []
        try:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError("cloudflared exited before connecting: " + "".join(tail)[-1000:])
                try:
                    line = lines.get(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                if line is None:
                    raise RuntimeError("cloudflared closed its log before connecting")
                if isinstance(line, BaseException):
                    raise RuntimeError(f"could not record cloudflared output: {line}") from line
                tail.append(line)
                tail = tail[-30:]
                if self.mode == "quick":
                    for match in QUICK_URL.findall(line):
                        parsed = urlsplit(match)
                        if parsed.hostname and parsed.hostname.lower() != "api.trycloudflare.com":
                            return parsed.scheme + "://" + parsed.netloc
                elif REGISTERED.search(line):
                    return "named"
            raise RuntimeError("cloudflared did not establish the tunnel before the startup deadline")
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if self.reader and self.reader.is_alive():
            self.reader.join(timeout=2)
        self.reader = None
        if self.config_path is not None:
            self.config_path.unlink(missing_ok=True)
            self.config_path = None


def wait_public_health(public_url: str, workspace_id: str, *, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = "public health endpoint did not respond"
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(public_url + "/health", headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(request, timeout=4) as response:
                payload = json.loads(response.read(64_000))
            if (payload.get("status") == "ok"
                    and payload.get("service") == "chatgpt-agent-mcp"
                    and payload.get("workspace_id") == workspace_id):
                return
            last_error = "public endpoint returned a different workspace identity"
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.5)
    raise RuntimeError("Cloudflare tunnel health check failed: " + last_error[:400])
