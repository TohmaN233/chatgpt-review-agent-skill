#!/usr/bin/env python3
"""Build a compact ChatGPT review packet from local repo files."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import zipfile

DEFAULT_EXCLUDES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
}


def repo_path(raw: str) -> pathlib.Path:
    return pathlib.Path(raw).resolve()


def safe_child(root: pathlib.Path, raw: str) -> pathlib.Path:
    path = (root / raw).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise SystemExit(f"Refusing path outside repo: {raw}") from exc
    if not path.is_file():
        raise SystemExit(f"File not found: {raw}")
    return path


def safe_dir(root: pathlib.Path, raw: str) -> pathlib.Path:
    path = (root / raw).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise SystemExit(f"Refusing directory outside repo: {raw}") from exc
    if not path.is_dir():
        raise SystemExit(f"Directory not found: {raw}")
    return path


def iter_dir_files(root: pathlib.Path, raw: str, includes: list[str]) -> list[tuple[str, pathlib.Path]]:
    base = safe_dir(root, raw)
    found: list[tuple[str, pathlib.Path]] = []
    for pattern in includes:
        for path in base.rglob(pattern):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(root).parts
            if any(part in DEFAULT_EXCLUDES for part in rel_parts):
                continue
            found.append((path.relative_to(root).as_posix(), path))
    return sorted(set(found), key=lambda item: item[0])


def numbered_text(path: pathlib.Path, max_bytes: int) -> tuple[str, bool]:
    data = path.read_bytes()
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    width = len(str(len(lines) or 1))
    return "\n".join(f"{i:>{width}}: {line}" for i, line in enumerate(lines, 1)), truncated


def fence_for(path: pathlib.Path) -> str:
    ext = path.suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".md": "markdown",
        ".html": "html",
        ".css": "css",
    }.get(ext, "text")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out", required=True)
    parser.add_argument("--zip")
    parser.add_argument("--goal", required=True)
    parser.add_argument("--claim", action="append", default=[])
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--dir", action="append", default=[])
    parser.add_argument(
        "--include",
        action="append",
        default=["*.py", "*.js", "*.ts", "*.tsx", "*.jsx", "*.md", "*.json", "*.yaml", "*.yml"],
    )
    parser.add_argument("--max-files", type=int, default=80)
    parser.add_argument("--max-bytes-per-file", type=int, default=30000)
    args = parser.parse_args()

    root = repo_path(args.repo)
    out = pathlib.Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    files = [(raw, safe_child(root, raw)) for raw in args.file]
    for raw_dir in args.dir:
        files.extend(iter_dir_files(root, raw_dir, args.include))
    deduped: dict[str, pathlib.Path] = {}
    for raw, path in files:
        deduped[path.relative_to(root).as_posix()] = path
    files = sorted(deduped.items(), key=lambda item: item[0])
    if len(files) > args.max_files:
        raise SystemExit(f"Too many files ({len(files)}). Narrow --dir/--include or raise --max-files.")

    parts = [
        "# ChatGPT Review Packet",
        "",
        f"- Generated: {dt.datetime.now(dt.timezone.utc).isoformat()}",
        f"- Repo: {root}",
        "",
        "## Goal",
        "",
        args.goal,
        "",
    ]
    if args.claim:
        parts += ["## Claims To Verify", ""]
        parts += [f"- {claim}" for claim in args.claim]
        parts += [""]
    parts += ["## Evidence", ""]
    for raw, path in files:
        rel = path.relative_to(root).as_posix()
        body, truncated = numbered_text(path, args.max_bytes_per_file)
        note = " (truncated)" if truncated else ""
        lang = fence_for(path)
        parts += [
            f"### {rel}{note}",
            "",
            f"```{lang}",
            body,
            "```",
            "",
        ]
    packet = "\n".join(parts)
    out.write_text(packet, encoding="utf-8")

    if args.zip:
        zip_path = pathlib.Path(args.zip).resolve()
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(out, "review-packet.md")
            for _raw, path in files:
                zf.write(path, f"files/{path.relative_to(root).as_posix()}")

    print(out)
    if args.zip:
        print(pathlib.Path(args.zip).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
