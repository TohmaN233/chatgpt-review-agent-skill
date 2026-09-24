"""Subprocess lifecycle and server-observed connector verification regressions."""
from __future__ import annotations
import concurrent.futures
import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

import agentctl
from tests.bridge_fixture import Client

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'agentctl.py'


class TestAgentCtl(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmpdir.name) / 'workspace'
        self.workspace.mkdir()
        self.state_home = Path(self.tmpdir.name) / 'state'
        self.env = {**os.environ, 'CHATGPT_AGENT_HOME': str(self.state_home)}

    def tearDown(self):
        self.run_cli('stop', '--json')
        self.tmpdir.cleanup()

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args, '--workspace', str(self.workspace)],
                              env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=18)

    def success(self, *args):
        result = self.run_cli(*args, '--json')
        detail = result.stderr + result.stdout
        if result.returncode:
            from bridge_runtime import sanitize
            secrets = [p.read_text().strip() for p in (self.state_home / 'tokens').glob('*.token')]
            for path in (self.state_home / 'logs').glob('*.log'):
                with path.open('rb') as log:
                    log.seek(max(0, path.stat().st_size - 12000))
                    detail += sanitize(log.read().decode('utf-8', errors='replace'),
                                       [self.workspace, self.state_home], secrets)
        self.assertEqual(result.returncode, 0, detail)
        return json.loads(result.stdout)

    def setup_start(self, **options):
        args = ['setup', '--port', '0']
        for key, value in options.items():
            args.extend(['--' + key.replace('_', '-'), value])
        setup = self.success(*args)
        start = self.success('start')
        self.assertNotIn('token', json.dumps(start).lower())
        wid = setup['workspace_id']
        token = (self.state_home / 'tokens' / (wid + '.token')).read_text().strip()
        return Client('http://127.0.0.1:' + str(setup['port']), token), setup, start

    def test_setup_start_real_receipt_doctor_stop(self):
        client, _, _ = self.setup_start()
        before = self.run_cli('doctor', '--json')
        self.assertEqual(before.returncode, 2)
        report = json.loads(before.stdout)
        self.assertTrue(report['local_ready'], report)
        self.assertEqual(report['checks']['tool_smoke'], 'unknown')
        no_receipt = self.run_cli('mark-verified', '--connector-name', 'test', '--json')
        self.assertNotEqual(no_receipt.returncode, 0)
        fake = self.run_cli('mark-verified', '--connector-name', 'test', '--receipt-id', 'invented', '--json')
        self.assertNotEqual(fake.returncode, 0)
        client.authorize()
        smoke = self.success('begin-smoke', '--connector-name', 'test')
        receipt = client.tool('workspace_info', {'smoke_challenge': smoke['challenge']})['result']['structuredContent']['smoke_receipt']
        self.success('mark-verified', '--connector-name', 'test', '--receipt-id', receipt['receipt_id'])
        after = json.loads(self.run_cli('doctor', '--json').stdout)
        self.assertEqual(after['checks']['tool_smoke'], 'pass')
        self.assertFalse(after['ready'])  # A loopback-only check is not public/ChatGPT readiness.
        self.assertEqual(after['checks']['public_endpoint'], 'not_configured')
        self.success('stop')
        self.assertFalse(self.success('status')['process_alive'])

    def test_restart_and_endpoint_change_invalidate_receipt(self):
        client, _, _ = self.setup_start()
        client.authorize()
        challenge = self.success('begin-smoke', '--connector-name', 'test')['challenge']
        rid = client.tool('workspace_info', {'smoke_challenge': challenge})['result']['structuredContent']['smoke_receipt']['receipt_id']
        self.success('mark-verified', '--connector-name', 'test', '--receipt-id', rid)
        self.success('stop')
        self.success('start')
        self.assertNotEqual(self.run_cli('mark-verified', '--connector-name', 'test', '--receipt-id', rid).returncode, 0)
        self.assertEqual(client.rpc('tools/list')[0], 200)
        self.success('set-endpoint', '--public-url', 'https://bridge.example')
        self.assertFalse(self.success('status')['process_alive'])
        self.assertIsNone(self.success('status')['receipt_id'])

    def test_idempotent_setup_start_and_serialized_concurrent_start(self):
        first = self.success('setup', '--port', '0')
        second = self.success('setup')
        self.assertEqual(first['port'], second['port'])
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            starts = list(pool.map(lambda _: self.success('start'), range(2)))
        self.assertEqual(sorted(value['reused'] for value in starts), [False, True])
        self.assertEqual(starts[0]['health']['boot_id'], starts[1]['health']['boot_id'])
        self.assertTrue(self.success('start')['reused'])

    def test_quick_tunnel_restart_reconciles_saved_connector_endpoint(self):
        state = {'tunnel_mode': 'quick', 'public_url': 'https://old.trycloudflare.com',
                 'connector_name': 'Workspace Connector',
                 'connector_endpoint': 'https://old.trycloudflare.com',
                 'receipt_id': 'old-receipt', 'tool_verified_at': 'old-time'}
        ping = {'endpoint': 'https://new.trycloudflare.com/mcp'}
        self.assertTrue(agentctl.sync_quick_tunnel_url(state, ping))
        self.assertEqual(state['public_url'], 'https://new.trycloudflare.com')
        self.assertIsNone(state['receipt_id'])
        self.assertEqual(agentctl.connector_action_for(state), 'update')
        state['connector_endpoint'] = state['public_url']
        self.assertFalse(agentctl.sync_quick_tunnel_url(state, ping))
        self.assertEqual(agentctl.connector_action_for(state), 'none')

    def test_doctor_reports_failed_probe_and_next_action(self):
        self.success('setup', '--port', '0')
        with patch.dict(os.environ, {'CHATGPT_AGENT_HOME': str(self.state_home)}), \
                patch.object(agentctl, 'control', side_effect=OSError('offline')):
            report = agentctl.doctor_report(self.workspace)
        self.assertEqual(report['checks']['process'], 'fail')
        self.assertEqual(report['diagnostics']['process']['error'], 'OSError')
        self.assertIn('agentctl start', report['action'])

    def test_task_lifecycle_through_local_cli(self):
        client, _, _ = self.setup_start(profile='implement')
        client.authorize()
        lease = self.success('grant-task', '--session-id', client.session, '--task-id', 'task-1',
                             '--profile', 'implement', '--path', 'src/**', '--ttl', '60')
        client.task_id = 'task-1'
        client.task_capability = lease['task_capability']
        client.lease_id = lease['lease_id']
        result = client.tool('write_text', {'task_id': 'task-1', 'operation_id': 'op-1',
                                         'path': 'src/file.txt', 'create': True, 'body': 'value'})
        self.assertIn('result', result)
        self.assertEqual(self.success('checkpoint', '--task-id', 'task-1')['operations'][0]['state'], 'done')
        self.success('revoke-task', '--session-id', client.session, '--task-id', 'task-1')
        result = client.tool('write_text', {'task_id': 'task-1', 'operation_id': 'op-2',
                                         'path': 'src/other.txt', 'create': True, 'body': 'value'})
        self.assertIn('error', result)
        self.success('disconnect', '--session-id', client.session)
        self.assertEqual(client.rpc('tools/list')[0], 401)

    def test_local_cli_persistent_bearer_connector_oauth_completes_directly(self):
        client, _, _ = self.setup_start()
        uri = 'https://chatgpt.com/connector/oauth/cli-enrollment-test'
        status, _, registered = client.request('/register', {
            'client_name': 'ChatGPT', 'redirect_uris': [uri]},
            headers={'Origin': 'https://chatgpt.com'})
        self.assertEqual(status, 201)
        clients = self.success('connector-clients')['clients']
        self.assertEqual(len(clients), 1)
        self.assertFalse(clients[0]['approved'])
        self.assertNotIn(registered['client_id'], json.dumps(clients))
        self.success('approve-client', '--client-fingerprint', clients[0]['client_fingerprint'])
        resource = client.control('ping')['endpoint']
        verifier = 'd' * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        authorization = {'client_id': registered['client_id'], 'redirect_uri': uri,
                         'response_type': 'code', 'resource': resource,
                         'state': 'cli-direct-bearer', 'code_challenge_method': 'S256',
                         'code_challenge': challenge}
        status, headers, _ = client.request(
            '/authorize?' + urlencode(authorization),
            headers={'Origin': 'https://chatgpt.com'})
        self.assertEqual(status, 302)
        code = parse_qs(urlsplit(headers['location']).query)['code'][0]
        status, _, tokens = client.request('/token', {
            'grant_type': 'authorization_code', 'client_id': registered['client_id'],
            'redirect_uri': uri, 'code_verifier': verifier, 'code': code,
            'resource': resource}, form=True,
            headers={'Origin': 'https://chatgpt.com'})
        self.assertEqual(status, 200)
        client.access = tokens['access_token']
        client.resource = resource
        self.assertEqual(client.rpc('tools/list')[0], 200)

    def test_profile_change_stops_old_permissions_and_start_uses_new_ceiling(self):
        client, _, _ = self.setup_start(profile='implement')
        client.authorize()
        self.success('profile', 'review', '--restart')
        self.assertEqual(client.rpc('tools/list')[0], 200)
        client.authorize()
        result = self.run_cli('grant-task', '--session-id', client.session, '--task-id', 'task',
                             '--profile', 'implement', '--path', 'src/**')
        self.assertNotEqual(result.returncode, 0)

    def test_setup_rejects_public_listener_insecure_endpoint_and_workspace_state(self):
        for extra in [('--host', '0.0.0.0'), ('--public-url', 'http://insecure.example')]:
            self.assertNotEqual(self.run_cli('setup', *extra).returncode, 0)
        env = {**self.env, 'CHATGPT_AGENT_HOME': str(self.workspace / 'host-state')}
        result = subprocess.run([sys.executable, str(SCRIPT), 'setup', '--workspace', str(self.workspace)],
                                env=env, text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)

    def test_direct_server_rejects_control_credential_under_exposed_root(self):
        credential = self.workspace / 'ordinary-name.txt'
        result = subprocess.run([sys.executable, str(ROOT / 'mcp_server.py'), '--root', str(self.workspace),
                                 '--token-file', str(credential), '--mcp-token-file',
                                 str(self.state_home / 'mcp-token'), '--state-dir', str(self.state_home)],
                                env=self.env, text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('outside every exposed root', result.stderr)
        self.assertFalse(credential.exists())

    def test_stale_pid_never_authorizes_killing_unrelated_service(self):
        setup = self.success('setup', '--port', '0')
        class Stranger(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(403); self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', setup['port']), Stranger)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
        thread.start()
        path = Path(setup['state_file'])
        state = json.loads(path.read_text())
        state['server_pid'] = os.getpid()
        path.write_text(json.dumps(state))
        try:
            self.assertNotEqual(self.run_cli('start').returncode, 0)
            self.assertNotEqual(self.run_cli('stop').returncode, 0)
            with socket.create_connection(('127.0.0.1', setup['port']), timeout=1):
                pass
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_doctor_does_not_accept_wrong_public_identity(self):
        client, _, _ = self.setup_start(public_url='https://bridge.example')
        client.authorize()
        challenge = self.success('begin-smoke', '--connector-name', 'test')['challenge']
        rid = client.tool('workspace_info', {'smoke_challenge': challenge})['result']['structuredContent']['smoke_receipt']['receipt_id']
        self.success('mark-verified', '--connector-name', 'test', '--receipt-id', rid)
        real_fetch = agentctl.fetch_json
        ping = client.control('ping')
        def fake_fetch(url, timeout=3):
            return (True, {**ping, 'workspace_id': 'wrong'}) if url.startswith('https://') else real_fetch(url, timeout)
        with patch.dict(os.environ, {'CHATGPT_AGENT_HOME': str(self.state_home)}), patch.object(agentctl, 'fetch_json', fake_fetch):
            report = agentctl.doctor_report(self.workspace)
            self.assertFalse(report['ready'])
            self.assertEqual(report['checks']['public_endpoint'], 'fail')
        def matching(url, timeout=3):
            return (True, ping) if url.startswith('https://') else real_fetch(url, timeout)
        # The simulated public leg checks the readiness predicate, not a real tunnel.
        with patch.dict(os.environ, {'CHATGPT_AGENT_HOME': str(self.state_home)}), patch.object(agentctl, 'fetch_json', matching):
            self.assertTrue(agentctl.doctor_report(self.workspace)['ready'])
