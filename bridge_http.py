"""HTTP/OAuth transport and deterministic per-call authorization boundary."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import html
import json
import re
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from socketserver import TCPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from bridge_runtime import MUTATIONS, sanitize
from bridge_security import Denied


def single_form(data: str) -> dict[str, str]:
    parsed = parse_qs(data, keep_blank_values=True, max_num_fields=32, strict_parsing=True)
    if any(len(values) != 1 for values in parsed.values()):
        raise Denied("duplicate_form_parameter")
    return {key: values[0] for key, values in parsed.items()}


def result(value: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}],
            "structuredContent": value}


class BridgeHTTP:
    def log_message(self, fmt, *args):
        # BaseHTTPRequestHandler would log query strings (codes/state). Receipts
        # and the mutation journal are the evidence channel, not access logs.
        pass

    def read_body(self) -> bytes:
        if self.headers.get("transfer-encoding") or len(self.headers.get_all("content-length", [])) != 1:
            raise Denied("invalid_request_framing")
        raw = self.headers.get("content-length", "")
        if not raw.isascii() or not raw.isdecimal() or not 0 <= int(raw) <= 2_000_000:
            raise Denied("invalid_content_length")
        data = self.rfile.read(int(raw))
        if len(data) != int(raw):
            raise Denied("incomplete_request_body")
        return data

    def external_base(self) -> str:
        return self.state.security.base

    def _request_context(self) -> dict:
        origin = self.headers.get("origin")
        if origin is not None:
            if len(origin) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in origin):
                origin = "[invalid]"
            elif origin != "null":
                parsed = urlsplit(origin)
                origin = (parsed.scheme + "://" + parsed.netloc
                          if parsed.scheme and parsed.netloc and not parsed.username
                          and not parsed.password else "[invalid]")
        host = self.headers.get("host")
        if host is not None and (len(host) > 256 or any(ord(char) < 33 or ord(char) > 126 for char in host)):
            host = "[invalid]"
        return {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                "workspace_id": self.state.security.workspace_id,
                "boot_id": self.state.security.boot_id,
                "method": self.command, "path": urlsplit(self.path).path,
                "host": host, "origin": origin}

    def _record_request_event(self, event: str, stage: str, outcome: str,
                              error: str | None = None, *,
                              client_id: str | None = None) -> None:
        record = {"event": event, "stage": stage, "outcome": outcome,
                  **self._request_context()}
        if error:
            record["error"] = (error if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", error)
                               else "invalid_error_code")
        if client_id and re.fullmatch(r"client_[A-Za-z0-9_-]{43}", client_id):
            record["client_fingerprint"] = hashlib.sha256(
                client_id.encode("ascii")).hexdigest()[:16]
        print(json.dumps(record), flush=True)

    def _record_transport_rejection(self, code: str) -> None:
        self._record_request_event("transport_rejected", "transport", "rejected", code)

    def _redirect_authorization(self, code: str, pending: dict) -> None:
        uri = pending["redirect_uri"]
        self.send_response(302)
        self.send_header("location", uri + ("&" if "?" in uri else "?")
                         + urlencode({"code": code, "state": pending["state"]}))
        self.send_header("content-length", "0")
        self.send_header("cache-control", "no-store")
        self.send_header("referrer-policy", "no-referrer")
        self.end_headers()

    def _transport_check(self, control=False, allow_connector_origin=False,
                         allow_opaque_authorization_origin=False,
                         allow_opaque_registration_origin=False):
        port = self.server.server_address[1]
        local_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        public_host = urlsplit(self.external_base()).netloc
        allowed = local_hosts if control else local_hosts | {public_host}
        if len(self.headers.get_all("host", [])) != 1 or self.headers["host"] not in allowed:
            self._record_transport_rejection("invalid_host")
            raise Denied("invalid_host")
        origins = {self.external_base(), *("http://" + host for host in local_hosts)}
        origin = self.headers.get("origin")
        chatgpt_connector_origin = (
            allow_connector_origin and origin == "https://chatgpt.com"
        )
        opaque_authorization_origin = (
            allow_opaque_authorization_origin and origin == "null"
        )
        opaque_registration_origin = (
            allow_opaque_registration_origin and origin == "null"
        )
        if (len(self.headers.get_all("origin", [])) > 1
                or (origin and (control or (origin not in origins
                                            and not chatgpt_connector_origin
                                            and not opaque_authorization_origin
                                            and not opaque_registration_origin)))):
            self._record_transport_rejection("invalid_origin")
            raise Denied("invalid_origin")
        if control:
            if (self.client_address[0] not in {"127.0.0.1", "::1"}
                    or len(self.headers.get_all("x-chatgpt-agent-admin", [])) != 1
                    or not secrets.compare_digest(self.headers["x-chatgpt-agent-admin"], self.state.token)):
                raise Denied("control_authorization_required")

    def _json_body(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise Denied("expected_json")
        def no_duplicates(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise Denied("duplicate_json_key")
                value[key] = item
            return value
        body = json.loads(self.read_body(), object_pairs_hook=no_duplicates,
                          parse_constant=lambda _: (_ for _ in ()).throw(Denied("invalid_json_number")))
        if not isinstance(body, dict):
            raise Denied("expected_json_object")
        return body

    def _form_body(self) -> dict:
        if self.headers.get_content_type() != "application/x-www-form-urlencoded":
            raise Denied("expected_form")
        return single_form(self.read_body().decode("utf-8"))

    def do_GET(self):
        path = urlsplit(self.path).path
        client_id = None
        try:
            self._transport_check(
                allow_connector_origin=path == "/authorize",
                allow_opaque_authorization_origin=path == "/authorize",
            )
            if path != "/health" and not getattr(self.server, "tunnel_ready", True):
                self.send_json(503, {"error": "tunnel_not_ready"})
                return
            gate = self.state.security
            if path == "/health":
                self.send_json(200, {"status": "ok", "service": "chatgpt-agent-mcp",
                    "workspace_id": gate.workspace_id, "boot_id": gate.boot_id,
                    "profile": self.state.profile, "endpoint": gate.resource,
                    "tunnel_ready": getattr(self.server, "tunnel_ready", True)})
            elif path in {"/.well-known/oauth-authorization-server", "/.well-known/oauth-authorization-server/mcp"}:
                base = gate.base
                grant_types = ["authorization_code"]
                if gate.persistent_bearer is None:
                    grant_types.append("refresh_token")
                self.send_json(200, {"issuer": base, "authorization_endpoint": base + "/authorize",
                    "token_endpoint": base + "/token", "registration_endpoint": base + "/register",
                    "revocation_endpoint": base + "/revoke", "response_types_supported": ["code"],
                    "grant_types_supported": grant_types,
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none"],
                    "scopes_supported": [gate.scope]})
            elif path in {"/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"}:
                self.send_json(200, {"resource": gate.resource, "authorization_servers": [gate.base],
                                    "scopes_supported": [gate.scope], "bearer_methods_supported": ["header"]})
            elif path == "/authorize":
                gate.throttle()
                form = single_form(urlsplit(self.path).query)
                client_id = form.get("client_id")
                nonce, pending = gate.begin_authorization(form)
                if gate.persistent_bearer is not None:
                    code, pending = gate.approve(nonce)
                    self._record_request_event(
                        "oauth_flow", "authorize", "bearer_authorized",
                        client_id=client_id)
                    self._redirect_authorization(code, pending)
                    return
                self._record_request_event(
                    "oauth_flow", "authorize", "consent_required",
                    client_id=client_id)
                consent_form = ("<form method='post' action='/authorize'><input type='hidden' name='consent' value='"
                                + html.escape(nonce, quote=True) + "'>")
                if gate.persistent_bearer is None:
                    consent_form += ("<label>Pairing code <input name='pairing_code' autocomplete='off' required></label>"
                                     "<button>Approve connection</button></form>")
                else:
                    consent_form += "<button>Approve this workspace connection</button></form>"
                page = ("<!doctype html><html><meta charset='utf-8'><title>Approve workspace connection</title>"
                        "<h1>Approve a workspace connection</h1><p>Client: " + html.escape(pending["client_name"])
                        + "</p><p>Redirect: " + html.escape(pending["redirect_uri"])
                        + "</p><p>Workspace: " + html.escape(gate.workspace_id)
                        + "</p><p>This authorizes reading configured roots. Writes require a separate host task lease.</p>"
                        + consent_form + "</html>")
                data = page.encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.send_header("content-length", str(len(data)))
                self.send_header("cache-control", "no-store")
                self.send_header("referrer-policy", "no-referrer")
                self.send_header("content-security-policy", "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
                secure = "; Secure" if gate.base.startswith("https:") else ""
                self.send_header("set-cookie", f"cga_consent={nonce}; HttpOnly; SameSite=Lax; Path=/authorize; Max-Age=300{secure}")
                self.end_headers()
                self.wfile.write(data)
            elif path == "/mcp":
                self.send_json(405, {"error": "method_not_allowed"}, {"allow": "POST"})
            else:
                self.send_json(404, {"error": "not_found"})
        except Denied as exc:
            if path == "/authorize":
                self._record_request_event(
                    "oauth_flow", "authorize", "rejected", str(exc),
                    client_id=client_id)
            self.send_json(400, {"error": str(exc)})
        except (ValueError, TypeError, UnicodeError):
            if path == "/authorize":
                self._record_request_event(
                    "oauth_flow", "authorize", "rejected", "invalid_request",
                    client_id=client_id)
            self.send_json(400, {"error": "invalid_request"})

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            # Authorization requests may arrive from ChatGPT's top-level
            # connector flow, including its opaque embedded-browser origin.
            # Exact Host validation still applies. Persistent-Bearer flows
            # finish on GET; this POST is used only by the pairing fallback.
            self._transport_check(
                control=path == "/control",
                allow_connector_origin=path in {"/register", "/authorize", "/token", "/revoke"},
                allow_opaque_authorization_origin=path == "/authorize",
                allow_opaque_registration_origin=path == "/register",
            )
            if path != "/control" and not getattr(self.server, "tunnel_ready", True):
                self.send_json(503, {"error": "tunnel_not_ready"})
                return
            gate = self.state.security
            if path == "/control":
                body = self._json_body()
                self.send_json(200, self._control(body))
                self.wfile.flush()
                if body.get("operation") == "stop":
                    def shutdown():
                        with self.state.runtime.lock:
                            self.server.shutdown()
                    threading.Thread(target=shutdown, daemon=True).start()
                return
            if path in {"/register", "/authorize", "/token", "/revoke"}:
                gate.throttle()
            if path == "/register":
                registration = gate.register(self._json_body())
                self._record_request_event(
                    "oauth_flow", "register", "registered",
                    client_id=registration["client_id"])
                self.send_json(201, registration)
            elif path == "/authorize":
                form = self._form_body()
                cookie = SimpleCookie()
                cookie.load(self.headers.get("cookie", ""))
                nonce = form.get("consent", "")
                if ("cga_consent" not in cookie or not nonce
                        or not secrets.compare_digest(cookie["cga_consent"].value, nonce)):
                    raise Denied("invalid_consent_cookie")
                code, pending = gate.approve(nonce, form.get("pairing_code", ""))
                self._record_request_event("oauth_flow", "authorize", "approved")
                self._redirect_authorization(code, pending)
            elif path == "/token":
                token_response = gate.exchange(self._form_body())
                self._record_request_event("oauth_flow", "token", "issued")
                self.send_json(200, token_response)
            elif path == "/revoke":
                form = self._form_body()
                gate.revoke_token(form.get("token", ""), form.get("client_id", ""))
                self._record_request_event("oauth_flow", "revoke", "revoked")
                self.send_json(200, {})
            elif path == "/mcp":
                auth = self.headers.get("authorization", "")
                try:
                    if len(self.headers.get_all("authorization", [])) != 1 or not auth.startswith("Bearer "):
                        raise Denied("invalid_token")
                    self.session_id = gate.authenticate(auth[7:])
                except Denied:
                    self.send_json(401, {"error": "invalid_token"}, {"www-authenticate":
                        'Bearer resource_metadata="' + gate.base + '/.well-known/oauth-protected-resource/mcp", '
                        'scope="' + gate.scope + '"'})
                    return
                request = self._json_body()
                rid = request.get("id")
                if (request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str)
                        or type(rid) not in {str, int, type(None)}
                        or ("params" in request and not isinstance(request["params"], dict))):
                    self.send_json(400, {"jsonrpc": "2.0", "id": None,
                                         "error": {"code": -32600, "message": "invalid request"}})
                    return
                if "id" not in request:
                    # Never execute a mutation notification: no retry identity or reply.
                    if not request["method"].startswith("notifications/"):
                        raise Denied("request_id_required")
                    self.send_response(202)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                try:
                    response = self.handle_rpc(request)
                except (ValueError, OSError, TypeError, KeyError) as exc:
                    message = sanitize(str(exc), [r.path for r in self.state.roots],
                                       [self.state.token, self.state.mcp_token or ""])
                    response = {"jsonrpc": "2.0", "id": rid,
                                "error": {"code": -32000, "message": message[:500]}}
                self.send_json(200, response)
            else:
                self.send_json(404, {"error": "not_found"})
        except Denied as exc:
            if path in {"/register", "/authorize", "/token", "/revoke"}:
                self._record_request_event("oauth_flow", path[1:], "rejected", str(exc))
            self.send_json(403 if path == "/control" else 400, {"error": str(exc)})
        except (ValueError, TypeError, UnicodeError, OSError):
            if path in {"/register", "/authorize", "/token", "/revoke"}:
                self._record_request_event("oauth_flow", path[1:], "rejected", "invalid_request")
            self.send_json(400, {"error": "invalid_request"})

    def _control(self, body: dict) -> dict:
        gate = self.state.security
        operation = body.get("operation")
        if operation == "ping":
            return {"workspace_id": gate.workspace_id, "boot_id": gate.boot_id,
                    "profile": self.state.profile, "endpoint": gate.resource,
                    "tunnel_ready": getattr(self.server, "tunnel_ready", True)}
        if operation == "pair":
            return gate.new_pairing()
        if operation == "probe":
            return gate.diagnostic_token()
        if operation == "sessions":
            with gate.lock:
                gate._prune()
                return {"sessions": [{"session_id": sid, **record} for sid, record in gate.sessions.items()
                                     if record["kind"] == "connector"]}
        if operation == "grant-task":
            return self.state.runtime.grant(body)
        if operation == "revoke-task":
            with gate.lock:
                key = (body.get("session_id", ""), body.get("task_id", ""))
                removed = gate.leases.pop(key, None)
            return {"revoked": removed is not None}
        if operation == "disconnect":
            session_id = body.get("session_id", "")
            if (gate.persistent_bearer is not None
                    and session_id == gate.persistent_session_id):
                gate.revoke_token(gate.persistent_bearer, "")
            else:
                gate.revoke_session(session_id)
            return {"revoked": True}
        if operation == "begin-smoke":
            return gate.begin_smoke(body.get("connector_name", ""))
        if operation == "mark-verified":
            return gate.verify_receipt(body.get("receipt_id", ""), body.get("connector_name", ""))
        if operation == "receipt-status":
            return {"receipt": gate.receipt_status(body.get("receipt_id", ""))}
        if operation == "connector-clients":
            return {"clients": gate.registered_client_summaries()}
        if operation == "approve-client":
            return gate.approve_registered_client(body.get("client_fingerprint", ""))
        if operation == "checkpoint":
            task_id = body.get("task_id", "")
            return self.state.runtime.journal.checkpoint(task_id)
        if operation == "stop":
            # Cancel leases first, let the validation worker kill/reap its child,
            # then stop accepting connections. Never abandon an active mutation.
            with gate.lock:
                for sid in list(gate.sessions):
                    gate.revoke_session(sid)
            return {"stopping": True, "boot_id": gate.boot_id}
        raise Denied("unknown_control_operation")

    def call_tool(self, name: str, args: dict) -> dict:
        gate = self.state.security
        with gate.lock:
            gate._prune()
            if getattr(self, "session_id", None) not in gate.sessions:
                raise Denied("authenticated_session_required")
        catalog = {tool["name"]: tool for tool in self.visible_tools()}
        if name not in catalog:
            raise Denied("tool_not_available")
        schema = catalog[name]["inputSchema"]
        if not isinstance(args, dict) or args.keys() - schema["properties"].keys():
            raise Denied("unknown_tool_arguments")
        if any(field not in args for field in schema.get("required", [])):
            raise Denied("missing_tool_arguments")
        for key, value in args.items():
            spec = schema["properties"][key]
            kind = spec["type"]
            if ((kind == "string" and not isinstance(value, str))
                    or (kind == "integer" and type(value) is not int)
                    or (kind == "boolean" and type(value) is not bool)):
                raise Denied("invalid_argument_type")
            if kind == "integer" and not spec.get("minimum", value) <= value <= spec.get("maximum", value):
                raise Denied("argument_out_of_range")
            if "enum" in spec and value not in spec["enum"]:
                raise Denied("invalid_argument_value")
        if name in MUTATIONS:
            self.mutation_guard = lambda: self.state.runtime.authorize(self.session_id, name, args)
            return self.state.runtime.mutate(self.session_id, name, args, lambda: self._execute_tool(name, args))
        value = self._execute_tool(name, args)
        if name == "workspace_info":
            data = value["structuredContent"]
            data.update({"boot_id": gate.boot_id})
            if args.get("smoke_challenge"):
                data["smoke_receipt"] = gate.observe_smoke(self.session_id, args["smoke_challenge"])
            return result(data)
        return value


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(32)
        super().__init__(*args, **kwargs)

    def server_bind(self):
        # HTTPServer.server_bind performs reverse DNS, which is not required
        # by this configured-origin protocol and is outside startup deadlines.
        TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = self.server_address[1]

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(10)
        return request, address

    def process_request(self, request, address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        # Do not emit request bytes, credentials or local tracebacks to a public log.
        pass
