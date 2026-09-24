from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "chatgpt-agent"
    / "scripts"
    / "build_packet.py"
)


class TestBuildPacket(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        # Host-selected output directories use canonical paths; /var on macOS
        # is an OS-level alias, not a link we intend the Packet to traverse.
        self.root = Path(self.tmpdir.name).resolve()
        (self.root / "src").mkdir()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def run_builder(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo",
                str(self.root),
                "--out",
                str(self.root / "out" / "packet.md"),
                "--zip",
                str(self.root / "out" / "packet.zip"),
                "--goal",
                "test goal",
                *extra,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_sensitive_explicit_file_is_rejected(self) -> None:
        (self.root / ".env").write_text("SECRET=x", encoding="utf-8")
        result = self.run_builder("--file", ".env")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing sensitive", result.stderr + result.stdout)

    def test_zip_uses_same_truncated_bytes_as_manifest(self) -> None:
        data = "abcdefghij" * 20
        (self.root / "src" / "large.txt").write_text(data, encoding="utf-8")
        result = self.run_builder(
            "--file",
            "src/large.txt",
            "--max-bytes-per-file",
            "25",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(
            (self.root / "out" / "manifest.json").read_text(encoding="utf-8")
        )
        item = manifest["files"][0]
        self.assertTrue(item["truncated"])
        self.assertEqual(item["included_bytes"], 25)
        packet = (self.root / "out" / "packet.md").read_text(encoding="utf-8")
        self.assertNotIn(str(self.root), packet)
        with zipfile.ZipFile(self.root / "out" / "packet.zip") as archive:
            included = archive.read("files/src/large.txt")
        self.assertEqual(len(included), 25)
        self.assertEqual(included, data.encode("utf-8")[:25])

    def test_custom_ignore_applies_to_explicit_file(self) -> None:
        (self.root / ".chatgpt-agentignore").write_text(
            "src/private.txt\n", encoding="utf-8"
        )
        (self.root / "src" / "private.txt").write_text(
            "private", encoding="utf-8"
        )
        result = self.run_builder("--file", "src/private.txt")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ignored path", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
