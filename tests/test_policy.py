from __future__ import annotations
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge_policy import atomic_write, checked_path, read_regular, relative_path
from tests.test_build_packet import SCRIPT


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temp.name).resolve()
        self.root = self.output_root / 'workspace'
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def packet(self, *extra, script=SCRIPT):
        return subprocess.run([sys.executable, str(script), '--repo', str(self.root),
                 '--out', str(self.output_root / 'packet.md'), '--zip', str(self.output_root / 'packet.zip'),
                 '--goal', 'regression', *extra], capture_output=True, text=True, timeout=10)

    def test_cross_platform_path_spelling_and_secret_aliases_rejected(self):
        for raw in ['../x', 'a/../x', '/absolute', 'C:/x', 'file:stream', 'a\\b', 'a//b', 'CON', 'lpt9.txt', 'bad.', 'bad ', 'line\nbreak']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                relative_path(raw)
        for raw in ['.ENV', 'folder/key.PEM', 'folder/.review-mcp-token', '.ssh/config']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                checked_path(self.root, raw, missing=True)

    def test_create_race_does_not_overwrite_winner_and_cleans_temp(self):
        actual_link = os.link
        def race(src, dst):
            Path(dst).write_text('winner')
            return actual_link(src, dst)
        with patch('os.link', race), self.assertRaises(FileExistsError):
            atomic_write(self.root, 'new.txt', b'loser', expected=None, create=True)
        self.assertEqual((self.root / 'new.txt').read_text(), 'winner')
        self.assertFalse(list(self.root.glob('.cga-write-*')))

    def test_cas_retains_permissions_and_commit_guard(self):
        path = self.root / 'script.py'
        path.write_bytes(b'old')
        path.chmod(0o755)
        expected = hashlib.sha256(b'old').hexdigest()
        def denied():
            raise ValueError('lease expired before commit')
        with self.assertRaisesRegex(ValueError, 'lease expired'):
            atomic_write(self.root, 'script.py', b'new', expected=expected, create=False, authorize=denied)
        self.assertEqual(path.read_bytes(), b'old')
        atomic_write(self.root, 'script.py', b'new', expected=expected, create=False)
        self.assertEqual(path.read_bytes(), b'new')
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)

    def test_packet_and_reads_reject_hardlinked_content(self):
        outside = Path(self.temp.name) / 'outside.txt'
        outside.write_text('outside-secret')
        os.link(outside, self.root / 'alias.txt')
        with self.assertRaises(ValueError):
            read_regular(self.root, 'alias.txt')
        self.assertNotEqual(self.packet('--file', 'alias.txt').returncode, 0)
        (self.root / 'safe.txt').write_text('safe')
        result = self.packet('--dir', '.')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('outside-secret', (self.output_root / 'packet.md').read_text())

    def test_directory_packet_never_follows_symlink_escape(self):
        outside = Path(self.temp.name) / 'outside.txt'
        outside.write_text('outside-secret')
        try:
            (self.root / 'alias.txt').symlink_to(outside)
        except OSError:
            self.skipTest('symlink creation unavailable to this OS account')
        (self.root / 'safe.txt').write_text('safe')
        result = self.packet('--dir', '.')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('outside-secret', (self.output_root / 'packet.md').read_text())
        self.assertNotEqual(self.packet('--file', 'alias.txt').returncode, 0)

    def test_negative_limits_and_output_over_evidence_are_rejected(self):
        path = self.root / 'source.txt'
        path.write_text('original')
        for flag in ['--max-files', '--max-bytes-per-file', '--max-total-bytes']:
            with self.subTest(flag=flag):
                self.assertNotEqual(self.packet('--file', 'source.txt', flag, '-1').returncode, 0)
        result = self.packet('--file', 'source.txt', '--out', str(path))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_text(), 'original')
        # Native CI supplies an 8.3 temporary prefix on Windows. This must
        # still collide with the canonical evidence identity, not overwrite it.
        raw_alias = Path(self.temp.name) / 'workspace/source.txt'
        alias_result = self.packet('--file', 'source.txt', '--out', str(raw_alias))
        self.assertNotEqual(alias_result.returncode, 0)
        self.assertEqual(path.read_text(), 'original')
        self.assertNotEqual(self.packet('--file', 'source.txt', '--manifest', str(self.output_root / 'packet.md')).returncode, 0)

    def test_output_link_is_rejected_before_canonicalization(self):
        (self.root / 'file.txt').write_text('evidence')
        target = self.output_root / 'target.txt'
        target.write_text('preserve')
        output = self.output_root / 'output-link.md'
        try:
            output.symlink_to(target)
        except OSError:
            self.skipTest('symlink creation unavailable to this OS account')
        result = self.packet('--file', 'file.txt', '--out', str(output))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_text(), 'preserve')

    def test_original_read_budget_not_just_truncated_size(self):
        path = self.root / 'large.txt'
        with path.open('wb') as handle:
            handle.truncate(16_000_001)
        result = self.packet('--file', 'large.txt', '--max-bytes-per-file', '1')
        self.assertNotEqual(result.returncode, 0)

    def test_standalone_skill_packet_builder_remains_executable(self):
        (self.root / 'file.txt').write_text('value')
        target = Path(self.temp.name) / 'isolated-skill'
        shutil.copytree(SCRIPT.parent, target, ignore=shutil.ignore_patterns('__pycache__'))
        result = self.packet('--file', 'file.txt', script=target / 'build_packet.py')
        self.assertEqual(result.returncode, 0, result.stderr)
