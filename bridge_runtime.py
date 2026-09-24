"""Host-bound mutation journal and bounded, sanitized execution evidence."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import signal
import sqlite3
import subprocess
import threading
import time
from contextlib import closing
from pathlib import Path

from bridge_policy import checked_path, ignore_patterns, relative_path
from bridge_security import Denied, Lease, identifier


MUTATIONS = {"write_artifact": "artifact.write", "write_review": "artifact.write",
             "write_text": "workspace.write", "run_validation": "validation.run"}


def sanitize(text: str, roots=(), secrets_to_hide=()) -> str:
    # Sanitize the entire retained stream BEFORE applying a display tail limit.
    text = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))", "", text)
    text = re.sub(r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?(?:-----END [^-\r\n]*PRIVATE KEY-----|\Z)",
                  "[REDACTED PRIVATE KEY]", text, flags=re.S)
    for value in sorted((str(s) for s in secrets_to_hide if s), key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)\b(?:cga_(?:at|rt|code)_|taskcap_|gh[pousr]_|github_pat_|sk-)[A-Za-z0-9_-]+",
                  "[REDACTED]", text)
    text = re.sub(r"(?i)(\b(?:authorization\s*[:=]\s*)?bearer\s+)[^\s\"'<>]+",
                  r"\1[REDACTED]", text)
    text = re.sub(r"(?im)([\"']?[\w.-]*(?:token|password|secret|api[_-]?key)[\w.-]*[\"']?\s*[:=]\s*)"
                  r"(?:\"[^\"]*\"|'[^']*'|[^\s,}]+)", r"\1[REDACTED]", text)
    for root in sorted((str(p) for p in roots if p), key=len, reverse=True):
        text = text.replace(root, "workspace:/").replace(root.replace("\\", "/"), "workspace:/")
    return "".join(c for c in text if c in "\n\r\t" or ord(c) >= 32)


class Journal:
    def __init__(self, directory: Path, workspace_id: str):
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink():
            raise Denied("linked_state_directory")
        os.chmod(directory, 0o700)
        self.path = directory / "operations.sqlite3"
        if self.path.is_symlink() or (self.path.exists() and self.path.stat().st_nlink != 1):
            raise Denied("linked_journal")
        with closing(self.connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS identity (workspace TEXT PRIMARY KEY)")
            rows = db.execute("SELECT workspace FROM identity").fetchall()
            if rows and rows != [(workspace_id,)]:
                raise Denied("journal_workspace_mismatch")
            db.execute("INSERT OR IGNORE INTO identity VALUES (?)", (workspace_id,))
            db.execute("CREATE TABLE IF NOT EXISTS task_index (lease_id TEXT PRIMARY KEY, task_id TEXT NOT NULL)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS task_index_by_task ON task_index(task_id)")
            db.execute("""CREATE TABLE IF NOT EXISTS operations (
                task TEXT NOT NULL, op TEXT NOT NULL, request_hash TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, PRIMARY KEY(task, op))""")
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def reserve(self, task: str, op: str, payload: dict) -> dict | None:
        request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True,
                                                  ensure_ascii=False).encode()).hexdigest()
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT request_hash,state,result FROM operations WHERE task=? AND op=?",
                             (task, op)).fetchone()
            if row:
                if row[0] != request_hash:
                    raise Denied("idempotency_key_conflict")
                if row[1] != "done":
                    raise Denied("operation_outcome_unknown_do_not_retry")
                return json.loads(row[2])
            db.execute("INSERT INTO operations VALUES (?,?,?,'pending',NULL)",
                       (task, op, request_hash))
        return None

    def finish(self, task: str, op: str, result: dict) -> None:
        with closing(self.connect()) as db, db:
            changed = db.execute("UPDATE operations SET state='done', result=? "
                                 "WHERE task=? AND op=? AND state='pending'",
                                 (json.dumps(result, ensure_ascii=False), task, op)).rowcount
            if changed != 1:
                raise Denied("journal_completion_conflict")

    def register_task(self, lease_id: str, task_id: str) -> None:
        lease_id, task_id = identifier(lease_id), identifier(task_id)
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT lease_id FROM task_index WHERE task_id=?", (task_id,)).fetchone()
            if row and row[0] != lease_id:
                raise Denied("task_journal_identity_conflict")
            db.execute("INSERT OR IGNORE INTO task_index VALUES (?,?)", (lease_id, task_id))

    def checkpoint(self, task: str) -> dict:
        task = identifier(task)
        with closing(self.connect()) as db:
            indexed = db.execute("SELECT lease_id FROM task_index WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
                                 (task,)).fetchone()
            journal_task = indexed[0] if indexed else task
            rows = db.execute("SELECT op,state FROM operations WHERE task=? ORDER BY op LIMIT 201",
                              (journal_task,)).fetchall()
        return {"task_id": task, "operations": [{"operation_id": op, "state": state}
                for op, state in rows[:200]], "truncated": len(rows) > 200}


class Runtime:
    def __init__(self, state, directory: Path, profiles: dict, validations: dict):
        self.state = state
        self.profiles = profiles
        self.validations = validations
        self.lock = threading.RLock()
        directory = directory.expanduser().resolve()
        for root in state.roots:
            if directory.is_relative_to(root.path):
                raise Denied("host_state_must_be_outside_all_exposed_roots")
        self.journal = Journal(directory, state.workspace_id)

    def base_commit(self) -> str | None:
        git = self.state.git_info()
        commit = git["commit"]
        if git["is_repo"] and (not isinstance(commit, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit)):
            raise Denied("git_commit_unavailable")
        return commit

    def grant(self, body: dict) -> dict:
        profile = body.get("profile", "review")
        if profile not in self.profiles:
            raise Denied("invalid_profile")
        paths, validations = body.get("paths", []), body.get("validations", [])
        if (not isinstance(paths, list) or len(paths) > 100
                or not isinstance(validations, list) or len(validations) > 20):
            raise Denied("invalid_lease_scope")
        clean_paths = []
        for raw in paths:
            if not isinstance(raw, str):
                raise Denied("invalid_lease_path")
            prefix = raw.endswith("/**")
            rel = raw[:-3] if prefix else raw
            relative_path(rel)
            self.state.safe_path(rel, allow_missing=True)
            clean_paths.append(rel + ("/**" if prefix else ""))
        if any(not isinstance(name, str) or name not in self.validations for name in validations):
            raise Denied("invalid_validation_name")
        caps = self.profiles[profile]
        if paths and "workspace.write" not in caps:
            raise Denied("profile_cannot_write_sources")
        if validations and "validation.run" not in caps:
            raise Denied("profile_cannot_run_validation")
        if not validations:
            caps = caps - {"validation.run"}
        if not self.lock.acquire(blocking=False):
            raise Denied("workspace_busy_retry_grant_after_current_operation")
        try:
            with self.state.security.lock:
                lease, task_capability = self.state.security.grant(
                    body.get("session_id", ""), body.get("task_id", ""),
                    profile, caps, tuple(clean_paths), tuple(validations), self.base_commit(), body.get("ttl", 900))
                try:
                    self.journal.register_task(lease.lease_id, lease.task_id)
                except BaseException:
                    self.state.security.leases.pop((lease.session_id, lease.task_id), None)
                    raise
        finally:
            self.lock.release()
        value = {"lease_id": lease.lease_id, "task_id": lease.task_id,
                 "session_id": lease.session_id, "workspace_id": lease.workspace_id,
                 "profile": lease.profile, "capabilities": sorted(lease.capabilities),
                 "paths": list(lease.paths), "validations": list(lease.validations),
                 "base_commit": lease.base_commit, "expires_at": lease.expires_at,
                 "task_capability": task_capability}
        return value

    def authorize(self, sid: str, name: str, args: dict) -> Lease:
        lease = self.state.security.require(sid, args.get("task_id", ""), MUTATIONS[name],
                                            args.get("task_capability", ""))
        identifier(args.get("operation_id"))
        if self.base_commit() != lease.base_commit:
            raise Denied("stale_task_base_commit")
        if name == "write_text":
            item, path = self.state.safe_path(args["path"], args.get("root_id"), allow_missing=True)
            if item.root_id != "root-0":
                raise Denied("task_writes_primary_root_only")
            rel = path.relative_to(item.path).as_posix()
            if not any(rel == p or (p.endswith("/**") and rel.startswith(p[:-3] + "/")) for p in lease.paths):
                raise Denied("path_outside_task_lease")
        if name == "run_validation" and args["name"] not in lease.validations:
            raise Denied("validation_not_granted")
        return lease

    def mutate(self, sid: str, name: str, args: dict, execute) -> dict:
        # Serializes all bridge-originating CAS operations and journal transitions.
        with self.lock:
            with self.state.security.lock:
                lease = self.authorize(sid, name, args)
                payload = {"name": name,
                           "args": {key: value for key, value in args.items()
                                    if key != "task_capability"},
                           "base_commit": lease.base_commit}
                cached = self.journal.reserve(lease.lease_id, args["operation_id"], payload)
                if cached is not None:
                    return cached
            if name == "run_validation":
                result = self.run_validation(lease, args)
            else:
                with self.state.security.lock:
                    self.authorize(sid, name, args)
                    result = execute()
            with self.state.security.lock:
                if name != "run_validation":
                    self.authorize(sid, name, args)
                self.journal.finish(lease.lease_id, args["operation_id"], result)
            return result

    def snapshot(self) -> dict:
        # Includes dirty tracked AND untracked content, not just `git status` names.
        info = self.state.git_info()
        h = hashlib.sha256()
        count = total = 0
        patterns = ignore_patterns(self.state.root)
        for base, dirs, files in os.walk(self.state.root, followlinks=False):
            allowed_dirs = []
            for directory in dirs:
                if directory in {".chatgpt-agent", ".chatgpt-review", "dist", "build"}:
                    continue
                try:
                    checked_path(self.state.root,
                                 (Path(base) / directory).relative_to(self.state.root).as_posix(),
                                 patterns=patterns)
                except ValueError:
                    continue
                allowed_dirs.append(directory)
            dirs[:] = sorted(allowed_dirs)
            for filename in sorted(files):
                path = Path(base) / filename
                rel = path.relative_to(self.state.root).as_posix()
                try:
                    checked_path(self.state.root, rel, patterns=patterns)
                except ValueError:
                    continue
                size = path.stat().st_size
                count += 1
                total += size
                if count > 10000 or total > 64_000_000:
                    raise Denied("snapshot_budget_exceeded")
                from bridge_policy import read_regular
                data = read_regular(self.state.root, rel)
                h.update(rel.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
        return {**info, "visible_content_sha256": h.hexdigest(), "files": count,
                "scope": "policy-visible workspace; excludes artifacts and dependencies"}

    def run_validation(self, lease: Lease, args: dict) -> dict:
        from mcp_server import tool_result
        before = self.snapshot()
        command = list(self.validations[args["name"]])
        # No arbitrary shell strings. npm.cmd must be found explicitly on Windows.
        if os.name == "nt" and command[0] == "npm":
            command[0] = "npm.cmd"
        timeout = min(args.get("timeout_seconds", 120), max(0, lease.expires_at - self.state.security.clock()))
        if timeout <= 0 or not self.state.security.active(lease):
            raise Denied("task_lease_required_or_expired")
        proc = subprocess.Popen(command, cwd=self.state.root, shell=False,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=os.name != "nt",
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        chunks: queue.Queue = queue.Queue(maxsize=8)
        stop = threading.Event()

        def reader():
            try:
                while not stop.is_set():
                    block = proc.stdout.read1(4096)
                    while not stop.is_set():
                        try:
                            chunks.put(block, timeout=.05)
                            break
                        except queue.Full:
                            continue
                    if not block:
                        break
            finally:
                proc.stdout.close()

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        data = bytearray()
        deadline = time.monotonic() + timeout
        outcome = "completed"
        cleanup_warning = None
        try:
            while True:
                if not self.state.security.active(lease):
                    outcome = "lease_revoked_or_expired"
                    break
                if time.monotonic() >= deadline:
                    outcome = "timeout"
                    break
                try:
                    block = chunks.get(timeout=.05)
                except queue.Empty:
                    continue
                if not block:
                    # EOF can precede process exit, or code can explicitly close
                    # stdout before doing more work. Continue enforcing the deadline.
                    while proc.poll() is None:
                        if not self.state.security.active(lease):
                            outcome = "lease_revoked_or_expired"
                            break
                        if time.monotonic() >= deadline:
                            outcome = "timeout"
                            break
                        time.sleep(.01)
                    break
                if len(data) + len(block) > 1_000_000:
                    outcome = "output_limit"
                    break
                data.extend(block)
        finally:
            if os.name != "nt":
                # Clean up the process group even when the leader has exited;
                # a child must not retain the output pipe after a timed-out run.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif proc.poll() is None:
                try:
                    killed = subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=5, check=False,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    if killed.returncode != 0 and proc.poll() is None:
                        cleanup_warning = "taskkill_failed"
                except (OSError, subprocess.TimeoutExpired) as exc:
                    cleanup_warning = type(exc).__name__
            if proc.poll() is None:
                try:
                    proc.terminate()
                except OSError as exc:
                    cleanup_warning = cleanup_warning or type(exc).__name__
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError as exc:
                    cleanup_warning = cleanup_warning or type(exc).__name__
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    cleanup_warning = "process_did_not_exit"
            stop.set()
            thread.join(timeout=2)
            if thread.is_alive():
                cleanup_warning = cleanup_warning or "output_reader_did_not_stop"
            if proc.poll() is None:
                cleanup_warning = cleanup_warning or "process_still_running"
            if cleanup_warning:
                # A validation result with uncertain process cleanup is never a
                # successful validation. Preserve the primary timeout/output-limit
                # outcome while exposing the cleanup problem to the caller.
                outcome = "cleanup_failed" if outcome == "completed" else outcome
        if outcome != "completed" and data and not data.endswith(b"\n"):
            # Never expose a credential fragment cut off at the retention limit.
            data = data[:data.rfind(b"\n") + 1]
        secret_values = [value for key, value in os.environ.items()
                         if re.search(r"(?i)token|secret|password|api.?key", key) and len(value) >= 8]
        clean = sanitize(data.decode("utf-8", errors="replace"),
                         [r.path for r in self.state.roots] + [Path.home()],
                         [self.state.token, *secret_values])
        encoded = clean.encode("utf-8")
        after = self.snapshot()
        evidence = {"task_id": lease.task_id, "workspace_id": self.state.workspace_id,
                    "operation_id": args["operation_id"], "base_commit": lease.base_commit,
                    "name": args["name"], "outcome": outcome, "exit_code": proc.returncode,
                    "success": outcome == "completed" and proc.returncode == 0,
                    "before": before, "after": after,
                    "output_sha256": hashlib.sha256(encoded).hexdigest(),
                    "truncated": len(encoded) > 40000 or outcome == "output_limit",
                    "output": encoded[-40000:].decode("utf-8", errors="replace")}
        if cleanup_warning:
            evidence["cleanup_warning"] = cleanup_warning
        return tool_result(evidence)
