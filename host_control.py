"""Local-owner control helpers; no connector credentials or shell interpolation."""
from __future__ import annotations

import contextlib
import json
import http.client
import os
import secrets
import stat
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def request_json(url: str, *, body=None, headers=None, timeout=3, local=False):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    if local:
        # Owner credentials may only travel directly to numeric loopback.
        # Do not initialize system proxy discovery or HTTPS trust stores for it.
        target = urlsplit(url)
        if (target.scheme != "http" or target.hostname != "127.0.0.1"
                or target.username or target.password or target.fragment):
            raise ValueError("local control requires numeric HTTP loopback")
        connection = http.client.HTTPConnection("127.0.0.1", target.port, timeout=timeout)
        try:
            path = target.path or "/"
            if target.query:
                path += "?" + target.query
            connection.request("GET" if data is None else "POST", path,
                               body=data, headers=request_headers)
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise urllib.error.HTTPError(url, response.status, "control request rejected", response.headers, None)
            raw = response.read(1_000_001)
        finally:
            connection.close()
    else:
        opener = urllib.request.build_opener(NoRedirect())
        request = urllib.request.Request(url, data=data, headers=request_headers)
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("response exceeds limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def private_token(path: Path | None, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    if path is None:
        return secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            meta = os.fstat(fd)
            if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or path.is_symlink():
                raise ValueError("control credential must be an ordinary private file")
            token = os.read(fd, 129).decode("ascii").strip()
            if not 32 <= len(token) <= 128:
                raise ValueError("invalid control credential file")
            return token
        finally:
            os.close(fd)
    token = secrets.token_urlsafe(32)
    with os.fdopen(fd, "w", encoding="ascii", newline="\n") as handle:
        handle.write(token + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return token


def replace_private_token(path: Path, token: str) -> None:
    """Atomically replace a credential in the private per-user state directory."""
    if not isinstance(token, str) or not 32 <= len(token) <= 128 or not token.isascii():
        raise ValueError("invalid private credential")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("credential file must not be a symlink")
    if path.exists():
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            meta = os.fstat(fd)
            if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
                raise ValueError("credential file must be an ordinary private file")
        finally:
            os.close(fd)
    fd, temporary = tempfile.mkstemp(prefix=".credential-", dir=path.parent)
    try:
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("linked state file")
    fd, tmp = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


@contextlib.contextmanager
def writer_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    locked = False
    try:
        if path.is_symlink() or os.fstat(fd).st_nlink != 1:
            raise ValueError("invalid control lock file")
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        deadline = time.monotonic() + 12
        while not locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError("another control operation is running") from None
                time.sleep(.05)
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
