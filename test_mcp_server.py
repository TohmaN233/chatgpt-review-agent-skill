#!/usr/bin/env python3
"""Stdlib-only tests for mcp_server.py safety checks."""

import tempfile
import unittest
from pathlib import Path

from mcp_server import DENY_GLOBS, DENY_NAMES, State


class TestSafetyChecks(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.state = State([self.root], None, None, True, True, False)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_safe_path_denies_git_directory(self):
        """safe_path should deny .git directory"""
        (self.root / ".git").mkdir()
        with self.assertRaises(ValueError) as ctx:
            self.state.safe_path(".git")
        self.assertIn("denied", str(ctx.exception))

    def test_safe_path_denies_node_modules(self):
        """safe_path should deny node_modules directory"""
        (self.root / "node_modules").mkdir()
        with self.assertRaises(ValueError) as ctx:
            self.state.safe_path("node_modules")
        self.assertIn("denied", str(ctx.exception))

    def test_safe_path_denies_env_file(self):
        """safe_path should deny .env files"""
        (self.root / ".env").touch()
        with self.assertRaises(ValueError) as ctx:
            self.state.safe_path(".env")
        self.assertIn("denied", str(ctx.exception))

    def test_safe_path_denies_private_key(self):
        """safe_path should deny .pem and .key files"""
        (self.root / "private.pem").touch()
        with self.assertRaises(ValueError) as ctx:
            self.state.safe_path("private.pem")
        self.assertIn("denied", str(ctx.exception))

        (self.root / "secret.key").touch()
        with self.assertRaises(ValueError) as ctx:
            self.state.safe_path("secret.key")
        self.assertIn("denied", str(ctx.exception))

    def test_safe_path_denies_ssh_keys(self):
        """safe_path should deny SSH key files"""
        (self.root / "id_rsa").touch()
        with self.assertRaises(ValueError) as ctx:
            self.state.safe_path("id_rsa")
        self.assertIn("denied", str(ctx.exception))

        (self.root / "id_ed25519").touch()
        with self.assertRaises(ValueError) as ctx:
            self.state.safe_path("id_ed25519")
        self.assertIn("denied", str(ctx.exception))

    def test_safe_path_denies_nested_denied_names(self):
        """safe_path should deny paths containing denied directories in nested paths"""
        (self.root / "subdir" / ".git").mkdir(parents=True)
        with self.assertRaises(ValueError) as ctx:
            self.state.safe_path("subdir/.git")
        self.assertIn("denied", str(ctx.exception))

    def test_safe_path_allows_normal_files(self):
        """safe_path should allow normal files"""
        (self.root / "README.md").touch()
        result = self.state.safe_path("README.md")
        self.assertEqual(result, self.root / "README.md")

    def test_safe_path_denies_escape(self):
        """safe_path should prevent directory traversal"""
        with self.assertRaises(ValueError) as ctx:
            self.state.safe_path("../etc/passwd")
        self.assertIn("escapes", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
