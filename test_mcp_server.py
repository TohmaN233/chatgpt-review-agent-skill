#!/usr/bin/env python3
"""Stdlib-only tests for the profile-aware MCP server."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mcp_server import Handler, State


class TestMcpServer(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.state = State([self.root], profile="review")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def handler(self, state: State | None = None) -> Handler:
        handler = object.__new__(Handler)
        handler.server = SimpleNamespace(state=state or self.state)
        return handler

    def test_safe_path_denies_sensitive_and_escape(self) -> None:
        (self.root / ".git").mkdir()
        (self.root / ".env").write_text("SECRET=x", encoding="utf-8")
        for raw in (".git", ".env", "../outside"):
            with self.assertRaises(ValueError, msg=raw):
                self.state.safe_path(raw)

    def test_safe_path_allows_env_template(self) -> None:
        path = self.root / ".env.example"
        path.write_text("NAME=value", encoding="utf-8")
        _root, resolved = self.state.safe_path(".env.example")
        self.assertEqual(resolved, path.resolve())
        self.assertTrue(resolved.samefile(path))

    def test_root_listing_hides_absolute_host_paths(self) -> None:
        result = self.handler()._execute_tool("list_allowed_roots", {})
        text = str(result)
        self.assertNotIn(str(self.root), text)
        self.assertIn("workspace:/", text)

    def test_profiles_expose_expected_tools(self) -> None:
        review_names = {
            tool["name"] for tool in self.handler().visible_tools()
        }
        self.assertIn("write_artifact", review_names)
        self.assertNotIn("write_text", review_names)
        self.assertNotIn("run_validation", review_names)

        implement = State([self.root], profile="implement")
        implement_names = {
            tool["name"]
            for tool in self.handler(implement).visible_tools()
        }
        self.assertIn("write_text", implement_names)
        self.assertIn("run_validation", implement_names)

    def test_existing_file_write_requires_matching_hash(self) -> None:
        path = self.root / "example.txt"
        path.write_text("old", encoding="utf-8")
        implement = State([self.root], profile="implement")
        handler = self.handler(implement)
        with self.assertRaises(ValueError):
            handler._execute_tool(
                "write_text", {"path": "example.txt", "body": "new"}
            )
        with self.assertRaises(ValueError):
            handler._execute_tool(
                "write_text",
                {
                    "path": "example.txt",
                    "body": "new",
                    "expected_sha256": "0" * 64,
                },
            )
        expected = hashlib.sha256(b"old").hexdigest()
        handler._execute_tool(
            "write_text",
            {
                "path": "example.txt",
                "body": "new",
                "expected_sha256": expected,
            },
        )
        self.assertEqual(path.read_text(encoding="utf-8"), "new")

    def test_new_file_requires_create_true(self) -> None:
        implement = State([self.root], profile="implement")
        handler = self.handler(implement)
        with self.assertRaises(ValueError):
            handler._execute_tool(
                "write_text", {"path": "new.txt", "body": "x"}
            )
        handler._execute_tool(
            "write_text",
            {"path": "new.txt", "body": "x", "create": True},
        )
        self.assertEqual(
            (self.root / "new.txt").read_text(encoding="utf-8"), "x"
        )


if __name__ == "__main__":
    unittest.main()
