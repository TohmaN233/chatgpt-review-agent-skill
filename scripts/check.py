#!/usr/bin/env python3
"""Run the same dependency-free checks locally and on every CI platform."""
from __future__ import annotations
import ast
import hashlib
import json
import os
import platform
import sys
import unittest
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    sources = []
    for directory, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {'.git', '.venv', 'venv', '__pycache__', 'node_modules', '.chatgpt-agent'}]
        for name in names:
            if name.endswith('.py'):
                path = Path(directory) / name
                ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
                sources.append(path)
    suite = unittest.defaultTestLoader.discover(str(root), pattern='test*.py', top_level_dir=str(root))
    if suite.countTestCases() < 50:
        raise SystemExit('Regression discovery is incomplete: expected at least 50 tests.')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {'python': platform.python_version(), 'platform': platform.system(),
               'parsed_python_files': len(sources), 'tests_run': result.testsRun,
               'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped),
               'success': result.wasSuccessful(), 'source_sha256': {
                   str(p.relative_to(root)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(sources)}}
    print(json.dumps(summary, sort_keys=True, indent=2), flush=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
