#!/usr/bin/env python3
"""Tiny MCP server for ChatGPT review workflows.

Stdlib-only on purpose. It exposes review reads, review writes, a tiny
shell allowlist, and opt-in source edits.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import mimetypes
import os
import secrets
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse


DENY_NAMES = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__"}
DENY_GLOBS = {"*.pem", "*.key", "*.p12", "*.pfx", ".env", ".env.*", "id_rsa", "id_ed25519"}


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def text_result(text: str) -> dict[str, object]:
    return {"content": [{"type": "text", "text": text}]}


def tool_result(value: object) -> dict[str, object]:
    result = text_result(json.dumps(value, ensure_ascii=False, indent=2))
    if isinstance(value, dict):
        result["structuredContent"] = value
    return result


def load_or_create_token(path: Path | None, explicit_token: str | None) -> str:
    if explicit_token:
        return explicit_token
    if path is None:
        return secrets.token_urlsafe(24)
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    return token


class State:
    def __init__(self, roots: list[Path], public_url: str | None, token: str | None, enable_write: bool, enable_shell: bool, enable_edit: bool):
        self.roots = [root.resolve() for root in roots]
        self.root = self.roots[0]
        self.public_url = (public_url or "").rstrip("/")
        self.token = token or secrets.token_urlsafe(24)
        self.codes: dict[str, float] = {}
        self.enable_write = enable_write
        self.enable_shell = enable_shell
        self.enable_edit = enable_edit
        self.review_dir = self.root / ".chatgpt-review"

    def root_for(self, root_id: str | None) -> Path:
        if not root_id:
            return self.root
        for root in self.roots:
            if root.name == root_id or str(root) == root_id:
                return root
        raise ValueError("unknown root_id")

    def safe_path(self, raw: str = ".", root_id: str | None = None) -> Path:
        root = self.root_for(root_id)
        target = (root / raw).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("path escapes allowed root") from exc
        parts = set(target.relative_to(root).parts)
        if parts & DENY_NAMES:
            raise ValueError("path is denied")
        for name in target.parts:
            if any(fnmatch.fnmatch(name, pat) for pat in DENY_GLOBS):
                raise ValueError("path is denied")
        return target

    def safe_review_path(self, raw: str) -> Path:
        target = (self.review_dir / raw).resolve()
        try:
            target.relative_to(self.review_dir)
        except ValueError as exc:
            raise ValueError("path escapes .chatgpt-review") from exc
        return target


class Handler(BaseHTTPRequestHandler):
    server_version = "tiny-review-mcp/0.1"

    @property
    def state(self) -> State:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.client_address[0]} - {fmt % args}")

    def send_json(self, status: int, value: object, headers: dict[str, str] | None = None) -> None:
        body = json_bytes(value)
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, status: int, value: str) -> None:
        body = value.encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "text/plain; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self) -> bytes:
        length = int(self.headers.get("content-length") or "0")
        return self.rfile.read(length)

    def authed(self) -> bool:
        auth = self.headers.get("authorization", "")
        return auth == f"Bearer {self.state.token}"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.send_json(200, {"status": "ok", "root": str(self.state.root)})
            return
        if parsed.path == "/.well-known/oauth-authorization-server":
            base = self.state.public_url or f"http://{self.headers.get('host')}"
            self.send_json(
                200,
                {
                    "issuer": base,
                    "authorization_endpoint": f"{base}/authorize",
                    "token_endpoint": f"{base}/token",
                    "registration_endpoint": f"{base}/register",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code"],
                    "token_endpoint_auth_methods_supported": ["none"],
                },
            )
            return
        if parsed.path == "/.well-known/oauth-protected-resource":
            base = self.state.public_url or f"http://{self.headers.get('host')}"
            self.send_json(200, {"resource": f"{base}/mcp", "authorization_servers": [base]})
            return
        if parsed.path == "/authorize":
            qs = parse_qs(parsed.query)
            redirect_uri = (qs.get("redirect_uri") or [""])[0]
            state = (qs.get("state") or [""])[0]
            code = secrets.token_urlsafe(18)
            self.state.codes[code] = time.time() + 300
            location = redirect_uri + ("&" if "?" in redirect_uri else "?") + urlencode({"code": code, "state": state})
            self.send_response(302)
            self.send_header("location", location)
            self.end_headers()
            return
        self.send_text(404, "not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/register":
            self.send_json(201, {"client_id": "tiny-review-mcp", "token_endpoint_auth_method": "none"})
            return
        if parsed.path == "/token":
            form = parse_qs(self.read_body().decode("utf-8", errors="replace"))
            code = (form.get("code") or [""])[0]
            if self.state.codes.pop(code, 0) < time.time():
                self.send_json(400, {"error": "invalid_grant"})
                return
            self.send_json(200, {"access_token": self.state.token, "token_type": "Bearer", "expires_in": 86400})
            return
        if parsed.path == "/mcp":
            if not self.authed():
                self.send_json(401, {"error": "unauthorized"}, {"www-authenticate": "Bearer"})
                return
            try:
                req = json.loads(self.read_body() or b"{}")
                self.send_json(200, self.handle_rpc(req))
            except Exception as exc:  # keep review server debuggable
                self.send_json(200, {"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": str(exc)}})
            return
        self.send_text(404, "not found")

    def handle_rpc(self, req: dict[str, object]) -> dict[str, object]:
        method = str(req.get("method") or "")
        req_id = req.get("id")
        params = req.get("params") if isinstance(req.get("params"), dict) else {}
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "tiny-review-mcp", "version": "0.1.0"},
                },
            }
        if method == "notifications/initialized":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        if method == "tools/list":
            tools = [*READ_TOOLS]
            if self.state.enable_write:
                tools += WRITE_TOOLS
            if self.state.enable_shell:
                tools += SHELL_TOOLS
            if self.state.enable_edit:
                tools += EDIT_TOOLS
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}
        if method == "tools/call":
            name = str(params.get("name") or "")
            args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            return {"jsonrpc": "2.0", "id": req_id, "result": self.call_tool(name, args)}
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"unknown method: {method}"}}

    def call_tool(self, name: str, args: dict[str, object]) -> dict[str, object]:
        if name == "list_allowed_roots":
            return tool_result({"roots": [{"root_id": root.name, "path": str(root), "name": root.name} for root in self.state.roots]})
        if name == "tree":
            rel = str(args.get("path") or ".")
            root_id = str(args.get("root_id") or "")
            max_entries = min(int(args.get("max_entries") or 30), 100)
            base = self.state.safe_path(rel, root_id)
            if not base.is_dir():
                raise ValueError("path is not a directory")
            rows = []
            for child in sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:max_entries]:
                if child.name in DENY_NAMES:
                    continue
                rows.append({"name": child.name, "type": "directory" if child.is_dir() else "file"})
            return tool_result({"path": rel, "entries": rows})
        if name == "read_text":
            rel = str(args.get("path") or "")
            root_id = str(args.get("root_id") or "")
            max_bytes = min(int(args.get("max_bytes") or 60000), 200000)
            path = self.state.safe_path(rel, root_id)
            if not path.is_file():
                raise ValueError("path is not a file")
            data = path.read_bytes()
            truncated = len(data) > max_bytes
            text = data[:max_bytes].decode("utf-8", errors="replace")
            return tool_result({"path": rel, "truncated": truncated, "text": text})
        if name == "search_text":
            needle = str(args.get("query") or "")
            rel = str(args.get("path") or ".")
            root_id = str(args.get("root_id") or "")
            glob = str(args.get("glob") or "*")
            max_results = min(int(args.get("max_results") or 20), 100)
            if not needle:
                raise ValueError("query is required")
            base = self.state.safe_path(rel, root_id)
            root = self.state.root_for(root_id)
            results = []
            for path in base.rglob(glob):
                if len(results) >= max_results:
                    break
                if not path.is_file():
                    continue
                try:
                    display_path = path.relative_to(root).as_posix()
                    text = path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                for idx, line in enumerate(text.splitlines(), 1):
                    if needle.lower() in line.lower():
                        results.append({"root_id": root.name, "path": display_path, "line": idx, "text": line[:500]})
                        break
            return tool_result({"query": needle, "results": results})
        if name == "write_review":
            if not self.state.enable_write:
                raise ValueError("write tools disabled")
            filename = str(args.get("name") or "review.md")
            body = str(args.get("body") or "")
            if not body.strip():
                raise ValueError("body is required")
            path = self.state.safe_review_path(filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            return tool_result({"path": path.relative_to(self.state.root).as_posix(), "bytes": len(body.encode("utf-8"))})
        if name == "list_review_artifacts":
            rows = []
            if self.state.review_dir.exists():
                for path in sorted(self.state.review_dir.rglob("*")):
                    if path.is_file():
                        rows.append({"path": path.relative_to(self.state.root).as_posix(), "bytes": path.stat().st_size})
            return tool_result({"artifacts": rows})
        if name == "run_command":
            if not self.state.enable_shell:
                raise ValueError("shell tools disabled")
            command = str(args.get("command") or "")
            if command not in ALLOWED_COMMANDS:
                raise ValueError(f"command not allowed: {command}")
            proc = subprocess.run(
                command,
                cwd=self.state.root,
                shell=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=min(int(args.get("timeout_seconds") or 60), 300),
            )
            return tool_result({"command": command, "exit_code": proc.returncode, "output": proc.stdout[-20000:]})
        if name == "write_text":
            if not self.state.enable_edit:
                raise ValueError("edit tools disabled; restart with --enable-edit")
            rel = str(args.get("path") or "")
            root_id = str(args.get("root_id") or "")
            body = str(args.get("body") or "")
            root = self.state.root_for(root_id)
            path = self.state.safe_path(rel, root_id)
            if path.exists() and not path.is_file():
                raise ValueError("path is not a file")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            return tool_result({"root_id": root.name, "path": path.relative_to(root).as_posix(), "bytes": len(body.encode("utf-8"))})
        raise ValueError(f"unknown tool: {name}")


READ_TOOLS = [
    {
        "name": "list_allowed_roots",
        "description": "List read-only roots available to this review MCP server.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "tree",
        "description": "List a small directory tree under the allowed root.",
        "inputSchema": {
            "type": "object",
            "properties": {"root_id": {"type": "string"}, "path": {"type": "string"}, "max_entries": {"type": "integer", "minimum": 1, "maximum": 100}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "read_text",
        "description": "Read a UTF-8 text file under the allowed root.",
        "inputSchema": {
            "type": "object",
            "properties": {"root_id": {"type": "string"}, "path": {"type": "string"}, "max_bytes": {"type": "integer", "minimum": 1, "maximum": 200000}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "search_text",
        "description": "Search text in files under the allowed root. Keep path and glob narrow.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "root_id": {"type": "string"},
                "path": {"type": "string"},
                "glob": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
]


WRITE_TOOLS = [
    {
        "name": "write_review",
        "description": "Write a review markdown artifact under .chatgpt-review/.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "body": {"type": "string"}},
            "required": ["body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_review_artifacts",
        "description": "List files under .chatgpt-review/.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
]


SHELL_TOOLS = [
    {
        "name": "run_command",
        "description": "Run one whitelisted local command in the repo root.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    }
]


EDIT_TOOLS = [
    {
        "name": "write_text",
        "description": "Overwrite a text file under the repo root. Disabled unless started with --enable-edit.",
        "inputSchema": {
            "type": "object",
            "properties": {"root_id": {"type": "string"}, "path": {"type": "string"}, "body": {"type": "string"}},
            "required": ["path", "body"],
            "additionalProperties": False,
        },
    }
]


ALLOWED_COMMANDS = {
    "git status --short",
    "git diff --stat",
    "git diff",
    "python -m pytest",
    "npm test",
}


for tool in [*READ_TOOLS, *WRITE_TOOLS, *SHELL_TOOLS, *EDIT_TOOLS]:
    tool.setdefault("outputSchema", {"type": "object", "additionalProperties": True})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", action="append", default=[], help="Root exposed to the reviewer; repeat for multiple roots")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--public-url", help="Public HTTPS base URL, e.g. https://mcp.example.com")
    parser.add_argument("--token", help="Static bearer token; generated if omitted")
    parser.add_argument("--token-file", help="Read or create a persistent bearer token in this file")
    parser.add_argument("--disable-write", action="store_true", help="Disable .chatgpt-review write tools")
    parser.add_argument("--disable-shell", action="store_true", help="Disable whitelisted shell command tool")
    parser.add_argument("--enable-edit", action="store_true", help="Enable write_text for repo files")
    args = parser.parse_args()

    roots = [Path(raw) for raw in (args.root or ["."])]
    token = load_or_create_token(Path(args.token_file) if args.token_file else None, args.token)
    state = State(roots, args.public_url, token, not args.disable_write, not args.disable_shell, args.enable_edit)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.state = state  # type: ignore[attr-defined]
    print("tiny-review-mcp roots:")
    for root in state.roots:
        print(f"  - {root.name}: {root}")
    print(f"local: http://{args.host}:{args.port}/mcp")
    if args.public_url:
        print(f"public: {args.public_url.rstrip('/')}/mcp")
    print(f"write tools: {state.enable_write}; shell tools: {state.enable_shell}; edit tools: {state.enable_edit}")
    print(f"token: {state.token}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
