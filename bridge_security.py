"""Host-owned OAuth sessions, task leases and connection receipts.

Short-lived OAuth credentials are hashed here. The workspace MCP bearer is
stored in a separate private per-user file and deliberately survives restarts.
Restart revokes sessions, leases and receipts; only the bearer and mutation
journal persist. The local control credential is never an MCP bearer.
"""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit


class Denied(ValueError):
    """A safe, public error code, without paths, credentials or request bodies."""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def opaque(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def bounded_text(value: object, name: str, limit: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise Denied("invalid_" + name)
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise Denied("invalid_" + name)
    return value


def identifier(value: object) -> str:
    value = bounded_text(value, "identifier", 128)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise Denied("invalid_identifier")
    return value


def https_base(value: str) -> str:
    value = bounded_text(value, "public_url", 2048).rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment
            or "\\" in value):
        raise Denied("public_url_must_be_an_https_origin")
    try:
        parsed.port
    except ValueError as exc:
        raise Denied("invalid_public_url") from exc
    return value


def redirect_uri(value: object) -> str:
    value = bounded_text(value, "redirect_uri", 2048)
    parsed = urlsplit(value)
    if (not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or "\\" in value):
        raise Denied("invalid_redirect_uri")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        raise Denied("invalid_redirect_uri")
    try:
        parsed.port
    except ValueError as exc:
        raise Denied("invalid_redirect_uri") from exc
    return value


@dataclass(frozen=True)
class Lease:
    lease_id: str
    task_id: str
    session_id: str
    workspace_id: str
    profile: str
    capabilities: frozenset[str]
    paths: tuple[str, ...]
    validations: tuple[str, ...]
    base_commit: str | None
    expires_at: float
    capability_hash: str


class SecurityGate:
    def __init__(self, workspace_id: str, maximum: frozenset[str], *, clock=time.time,
                 persistent_bearer: str | None = None, rotate_bearer=None,
                 registered_clients: dict[str, dict] | None = None, persist_clients=None):
        self.workspace_id = workspace_id
        self.maximum = maximum
        self.clock = clock
        self.boot_id = opaque("boot_")
        self.scope = "workspace:" + workspace_id
        self.base = ""
        self.lock = threading.RLock()
        self.persist_clients = persist_clients
        previous_clients = registered_clients or {}
        self.clients: dict[str, dict] = self._validate_registered_clients(previous_clients)
        migrated_clients = any(
            isinstance(record, dict)
            and ("trusted_redirects" in record or "expires_at" in record)
            for record in previous_clients.values()
        )
        if migrated_clients and self.persist_clients is not None:
            self.persist_clients(self.clients)
        self.pending: dict[str, dict] = {}
        self.codes: dict[str, dict] = {}
        self.access: dict[str, dict] = {}
        self.refresh: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.leases: dict[tuple[str, str], Lease] = {}
        self.completed_consents: dict[str, dict] = {}
        self.pairing: dict | None = None
        self.challenge: dict | None = None
        self.receipts: dict[str, dict] = {}
        self.rate_window = 0.0
        self.rate_count = 0
        self.persistent_bearer = persistent_bearer
        self.persistent_bearer_hash = digest(persistent_bearer) if persistent_bearer else None
        self.persistent_session_id: str | None = None
        self.rotate_bearer = rotate_bearer

    @staticmethod
    def _validate_registered_clients(clients: dict[str, dict]) -> dict[str, dict]:
        if not isinstance(clients, dict) or len(clients) > 128:
            raise ValueError("invalid OAuth client registry")
        validated = {}
        for client_id, record in clients.items():
            if (not isinstance(client_id, str)
                    or not re.fullmatch(r"client_[A-Za-z0-9_-]{43}", client_id)
                    or not isinstance(record, dict)):
                raise ValueError("invalid OAuth client registry entry")
            redirect_uris = record.get("redirect_uris")
            if not isinstance(redirect_uris, list) or not 1 <= len(redirect_uris) <= 8:
                raise ValueError("invalid OAuth client redirect registry")
            validated[client_id] = {
                "redirect_uris": list(dict.fromkeys(redirect_uri(uri) for uri in redirect_uris)),
                "client_name": bounded_text(record.get("client_name"), "client_name", 120),
                # DCR registration records a client; it does not authorize it
                # to receive the workspace Bearer. Legacy entries migrate as
                # unapproved and are bound by the local owner once.
                "approved": record.get("approved", False),
            }
            if type(validated[client_id]["approved"]) is not bool:
                raise ValueError("invalid OAuth client approval state")
        return validated

    @property
    def resource(self) -> str:
        return self.base + "/mcp"

    def configure(self, base: str) -> None:
        # Called once by the host after binding the listening port, never from
        # a Host/X-Forwarded-* header supplied by an untrusted client.
        if self.base:
            raise Denied("issuer_already_configured")
        self.base = base.rstrip("/")

    def _prune(self) -> None:
        now = self.clock()
        for mapping in (self.clients, self.pending, self.codes, self.access,
                        self.refresh, self.sessions, self.receipts,
                        self.completed_consents):
            for key in list(mapping):
                expires_at = mapping[key].get("expires_at")
                if expires_at is not None and expires_at <= now:
                    del mapping[key]
        for key in list(self.leases):
            lease = self.leases[key]
            if lease.expires_at <= now or lease.session_id not in self.sessions:
                del self.leases[key]

    def throttle(self) -> None:
        with self.lock:
            now = self.clock()
            if now - self.rate_window >= 60:
                self.rate_window, self.rate_count = now, 0
            self.rate_count += 1
            if self.rate_count > 120:
                raise Denied("rate_limited")
            self._prune()

    def register(self, body: dict) -> dict:
        with self.lock:
            self._prune()
            if len(self.clients) >= 128:
                raise Denied("registration_capacity_exceeded")
            uris = body.get("redirect_uris")
            if not isinstance(uris, list) or not 1 <= len(uris) <= 8:
                raise Denied("invalid_redirect_uris")
            uris = list(dict.fromkeys(redirect_uri(uri) for uri in uris))
            if body.get("token_endpoint_auth_method", "none") != "none":
                raise Denied("unsupported_client_authentication")
            if body.get("response_types", ["code"]) != ["code"]:
                raise Denied("unsupported_response_type")
            grants = body.get("grant_types", ["authorization_code", "refresh_token"])
            if (not isinstance(grants, list) or "authorization_code" not in grants
                    or any(g not in {"authorization_code", "refresh_token"} for g in grants)):
                raise Denied("unsupported_grant_type")
            client = opaque("client_")
            name = bounded_text(body.get("client_name", "MCP client"), "client_name", 120)
            self.clients[client] = {"redirect_uris": uris, "client_name": name,
                                    "approved": False}
            if self.persist_clients is not None:
                try:
                    self.persist_clients(self.clients)
                except BaseException:
                    del self.clients[client]
                    raise
            supported_grants = ["authorization_code"]
            if self.persistent_bearer is None:
                supported_grants.append("refresh_token")
            return {"client_id": client, "redirect_uris": uris, "client_name": name,
                    "token_endpoint_auth_method": "none", "response_types": ["code"],
                    "grant_types": supported_grants}

    def registered_client_summaries(self) -> list[dict]:
        with self.lock:
            return [
                {"client_fingerprint": hashlib.sha256(client_id.encode("ascii")).hexdigest()[:16],
                 "client_name": record["client_name"],
                 "redirect_uris": list(record["redirect_uris"]),
                 "approved": bool(record["approved"])}
                for client_id, record in self.clients.items()
            ]

    def approve_registered_client(self, fingerprint: object) -> dict:
        """Bind one registered ChatGPT client to this workspace locally."""
        if (not isinstance(fingerprint, str)
                or not re.fullmatch(r"[a-f0-9]{16}", fingerprint)):
            raise Denied("invalid_client_fingerprint")
        with self.lock:
            matches = [
                (client_id, record) for client_id, record in self.clients.items()
                if hashlib.sha256(client_id.encode("ascii")).hexdigest()[:16]
                == fingerprint
            ]
            if len(matches) != 1:
                raise Denied("registered_client_not_found")
            client_id, record = matches[0]
            if record["client_name"] != "ChatGPT":
                raise Denied("unexpected_client_name")
            callbacks = record["redirect_uris"]
            if not callbacks or any(
                    not (urlsplit(uri).scheme == "https"
                         and urlsplit(uri).hostname == "chatgpt.com"
                         and urlsplit(uri).port is None
                         and re.fullmatch(
                             r"/connector/oauth/[A-Za-z0-9_-]{8,128}",
                             urlsplit(uri).path))
                    for uri in callbacks):
                raise Denied("invalid_connector_redirect")
            if not record["approved"]:
                record["approved"] = True
                try:
                    if self.persist_clients is not None:
                        self.persist_clients(self.clients)
                except BaseException:
                    record["approved"] = False
                    raise
            return {"workspace_id": self.workspace_id,
                    "client_fingerprint": fingerprint,
                    "client_name": record["client_name"],
                    "redirect_uris": list(callbacks), "approved": True}

    def new_pairing(self) -> dict:
        with self.lock:
            if self.persistent_bearer is not None:
                raise Denied("pairing_disabled_for_persistent_bearer")
            code = secrets.token_hex(8).upper()
            self.pairing = {"hash": digest(code), "expires_at": self.clock() + 300,
                            "attempts": 0}
            return {"pairing_code": code, "expires_in": 300}

    def begin_authorization(self, form: dict) -> tuple[str, dict]:
        with self.lock:
            self._prune()
            client = self.clients.get(form.get("client_id", ""))
            if not client:
                raise Denied("invalid_client")
            if form.get("response_type") != "code":
                raise Denied("unsupported_response_type")
            uri = form.get("redirect_uri")
            if uri not in client["redirect_uris"]:
                raise Denied("invalid_redirect_uri")
            if self.persistent_bearer is not None:
                parsed_uri = urlsplit(uri)
                if (parsed_uri.scheme != "https"
                        or parsed_uri.hostname != "chatgpt.com"
                        or parsed_uri.port is not None
                        or not re.fullmatch(r"/connector/oauth/[A-Za-z0-9_-]{8,128}", parsed_uri.path)):
                    raise Denied("invalid_connector_redirect")
            challenge = form.get("code_challenge", "")
            if (form.get("code_challenge_method") != "S256"
                    or not re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge)):
                raise Denied("invalid_pkce_challenge")
            if form.get("resource") != self.resource:
                raise Denied("invalid_target")
            if form.get("scope", self.scope) != self.scope:
                raise Denied("invalid_scope")
            if (self.persistent_bearer is not None
                    and not client.get("approved", False)):
                raise Denied("client_not_approved")
            if len(self.pending) >= 128:
                raise Denied("authorization_capacity_exceeded")
            nonce = opaque("consent_")
            record = {"client_id": form["client_id"], "redirect_uri": uri,
                      "challenge": challenge,
                      "state": bounded_text(form.get("state"), "state", 1024),
                      "resource": self.resource,
                      "expires_at": self.clock() + 300}
            self.pending[digest(nonce)] = record
            return nonce, {**record, "client_name": client["client_name"]}

    def approve(self, nonce: str, pairing_code: str = "") -> tuple[str, dict]:
        with self.lock:
            self._prune()
            nonce_hash = digest(nonce)
            pending = self.pending.get(nonce_hash)
            if not pending:
                completed = self.completed_consents.get(nonce_hash)
                if completed:
                    return completed["code"], dict(completed["pending"])
                raise Denied("invalid_or_expired_consent")
            if self.persistent_bearer is None:
                pairing = self.pairing
                if not pairing or pairing["expires_at"] <= self.clock():
                    raise Denied("invalid_or_expired_consent")
                pairing["attempts"] += 1
                if pairing["attempts"] > 5:
                    self.pairing = None
                    raise Denied("pairing_locked")
                if not secrets.compare_digest(pairing["hash"], digest(pairing_code)):
                    raise Denied("invalid_pairing_code")
                self.pairing = None
            code = opaque("cga_code_")
            # Keep the single-use PKCE-bound code valid long enough for the
            # connector's browser callback and server-side token exchange.
            self.codes[digest(code)] = {**pending, "expires_at": self.clock() + 300}
            del self.pending[nonce_hash]
            self.completed_consents[nonce_hash] = {
                "code": code, "pending": dict(pending),
                "expires_at": self.clock() + 60}
            return code, pending

    def _new_session(self, client_id: str, kind: str = "connector") -> str:
        self._prune()
        if len(self.sessions) >= 128:
            raise Denied("session_capacity_exceeded")
        sid = opaque("session_")
        lifetime = 86400 if kind == "connector" else 60
        self.sessions[sid] = {"client_id": client_id, "kind": kind,
                              "expires_at": self.clock() + lifetime}
        return sid

    def _tokens(self, sid: str) -> dict:
        session = self.sessions[sid]
        now = self.clock()
        # Capacity is bounded even for repeated refresh attempts. Revoked/used
        # refresh hashes are retained until session expiry to detect replay.
        if len(self.refresh) >= 4096 or len(self.access) >= 4096:
            raise Denied("token_capacity_exceeded")
        token = opaque("cga_at_")
        self.access[digest(token)] = {"session_id": sid,
                                     "expires_at": min(now + 900, session["expires_at"])}
        result = {"access_token": token, "token_type": "Bearer",
                  "expires_in": int(min(900, session["expires_at"] - now)),
                  "scope": self.scope}
        if session["kind"] == "connector":
            refresh = opaque("cga_rt_")
            self.refresh[digest(refresh)] = {"session_id": sid, "used": False,
                                            "expires_at": session["expires_at"]}
            result["refresh_token"] = refresh
        return result

    def exchange(self, form: dict) -> dict:
        with self.lock:
            self._prune()
            if form.get("resource") != self.resource:
                raise Denied("invalid_target")
            if form.get("grant_type") == "authorization_code":
                # Consume even an invalid exchange: never leave a raced code usable.
                code = self.codes.pop(digest(form.get("code", "")), None)
                verifier = form.get("code_verifier", "")
                if (not code or form.get("client_id") != code["client_id"]
                        or form.get("redirect_uri") != code["redirect_uri"]
                        or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)):
                    raise Denied("invalid_grant")
                challenge = base64.urlsafe_b64encode(
                    hashlib.sha256(verifier.encode("ascii")).digest()
                ).decode("ascii").rstrip("=")
                if not secrets.compare_digest(code["challenge"], challenge):
                    raise Denied("invalid_grant")
                if self.persistent_bearer is not None:
                    client = self.clients.get(code["client_id"])
                    if not client or not client.get("approved", False):
                        raise Denied("invalid_grant")
                    return {"access_token": self.persistent_bearer,
                            "token_type": "Bearer", "scope": self.scope}
                sid = self._new_session(code["client_id"])
                return self._tokens(sid)
            if form.get("grant_type") == "refresh_token":
                old = self.refresh.get(digest(form.get("refresh_token", "")))
                session = self.sessions.get(old["session_id"]) if old else None
                if not session or form.get("client_id") != session["client_id"]:
                    raise Denied("invalid_grant")
                if old["used"]:
                    self.revoke_session(old["session_id"])
                    raise Denied("refresh_reuse_session_revoked")
                old["used"] = True
                # Old access tokens stop working at rotation; existing leases
                # remain bound to the same session, not to an untrusted task ID.
                for key in list(self.access):
                    if self.access[key]["session_id"] == old["session_id"]:
                        del self.access[key]
                return self._tokens(old["session_id"])
            raise Denied("unsupported_grant_type")

    def authenticate(self, token: str) -> str:
        with self.lock:
            self._prune()
            if (self.persistent_bearer_hash is not None
                    and secrets.compare_digest(digest(token), self.persistent_bearer_hash)):
                session = self.sessions.get(self.persistent_session_id or "")
                if session is None:
                    self.persistent_session_id = self._new_session("workspace-connector")
                    session = self.sessions[self.persistent_session_id]
                else:
                    session["expires_at"] = self.clock() + 86400
                return self.persistent_session_id
            record = self.access.get(digest(token))
            if not record or record["session_id"] not in self.sessions:
                raise Denied("invalid_token")
            return record["session_id"]

    def diagnostic_token(self) -> dict:
        with self.lock:
            sid = self._new_session("host-diagnostic", "diagnostic")
            return self._tokens(sid)

    def revoke_session(self, sid: str) -> None:
        with self.lock:
            self.sessions.pop(sid, None)
            for mapping in (self.access, self.refresh):
                for key in list(mapping):
                    if mapping[key]["session_id"] == sid:
                        del mapping[key]
            for key in [key for key, lease in self.leases.items() if lease.session_id == sid]:
                del self.leases[key]
            for key in list(self.receipts):
                if self.receipts[key]["session_id"] == sid:
                    del self.receipts[key]

    def revoke_token(self, token: str, client_id: str) -> None:
        with self.lock:
            if (self.persistent_bearer_hash is not None
                    and secrets.compare_digest(digest(token), self.persistent_bearer_hash)):
                if self.rotate_bearer is None:
                    raise Denied("persistent_token_rotation_unavailable")
                replacement = opaque("cga_at_")
                self.rotate_bearer(replacement)
                sid = self.persistent_session_id
                self.persistent_bearer = replacement
                self.persistent_bearer_hash = digest(replacement)
                self.persistent_session_id = None
                if sid:
                    self.revoke_session(sid)
                return
            record = self.access.get(digest(token)) or self.refresh.get(digest(token))
            session = self.sessions.get(record["session_id"]) if record else None
            if session and session["client_id"] == client_id:
                self.revoke_session(record["session_id"])

    def grant(self, sid: str, task_id: str, profile: str, capabilities: frozenset[str],
              paths: tuple[str, ...], validations: tuple[str, ...],
              base_commit: str | None, ttl: int) -> tuple[Lease, str]:
        with self.lock:
            self._prune()
            if sid not in self.sessions or self.sessions[sid]["kind"] != "connector":
                raise Denied("invalid_session")
            if not capabilities <= self.maximum:
                raise Denied("profile_exceeds_host_ceiling")
            if type(ttl) is not int or not 1 <= ttl <= 3600:
                raise Denied("invalid_lease_ttl")
            task_id = identifier(task_id) if task_id else opaque("task_")
            if any(other_task == task_id for _, other_task in self.leases):
                raise Denied("task_id_already_leased")
            task_capability = opaque("taskcap_")
            lease = Lease(opaque("lease_"), task_id, sid, self.workspace_id,
                          profile, capabilities, paths, validations, base_commit,
                          min(self.clock() + ttl, self.sessions[sid]["expires_at"]),
                          digest(task_capability))
            self.leases[(sid, task_id)] = lease
            return lease, task_capability

    def require(self, sid: str, task_id: str, capability: str,
                task_capability: str) -> Lease:
        with self.lock:
            self._prune()
            lease = self.leases.get((sid, task_id))
            if (not lease or lease.task_id != task_id or capability not in lease.capabilities
                    or lease.workspace_id != self.workspace_id
                    or not isinstance(task_capability, str)
                    or not re.fullmatch(r"taskcap_[A-Za-z0-9_-]{43}", task_capability)
                    or not secrets.compare_digest(lease.capability_hash, digest(task_capability))):
                raise Denied("task_lease_required_or_expired")
            return lease

    def active(self, lease: Lease) -> bool:
        with self.lock:
            self._prune()
            return self.leases.get((lease.session_id, lease.task_id)) == lease

    def begin_smoke(self, connector_name: str) -> dict:
        with self.lock:
            nonce = opaque("smoke_")
            self.challenge = {"hash": digest(nonce), "expires_at": self.clock() + 300,
                              "connector_name": bounded_text(connector_name, "connector_name", 120)}
            return {"challenge": nonce, "expires_in": 300, "workspace_id": self.workspace_id,
                    "boot_id": self.boot_id, "tool": "workspace_info"}

    def observe_smoke(self, sid: str, nonce: str) -> dict:
        with self.lock:
            self._prune()
            session = self.sessions.get(sid)
            pending = self.challenge
            if (not session or session["kind"] != "connector" or not pending
                    or pending["expires_at"] <= self.clock()
                    or not secrets.compare_digest(pending["hash"], digest(nonce))):
                raise Denied("invalid_smoke_challenge")
            rid = opaque("receipt_")
            record = {"receipt_id": rid, "session_id": sid, "workspace_id": self.workspace_id,
                      "boot_id": self.boot_id, "endpoint": self.resource,
                      "connector_name": pending["connector_name"], "tool": "workspace_info",
                      "observed_at": self.clock(), "expires_at": self.clock() + 3600,
                      "verified": False, "provenance": "authenticated_connector_call"}
            self.receipts[rid] = record
            self.challenge = None
            return dict(record)

    def verify_receipt(self, rid: str, name: str) -> dict:
        with self.lock:
            self._prune()
            record = self.receipts.get(rid)
            if not record or record["connector_name"] != name:
                raise Denied("invalid_smoke_receipt")
            record["verified"] = True
            return dict(record)

    def receipt_status(self, rid: str) -> dict | None:
        with self.lock:
            self._prune()
            record = self.receipts.get(rid)
            if not record or not record["verified"] or record["session_id"] not in self.sessions:
                return None
            return dict(record)
