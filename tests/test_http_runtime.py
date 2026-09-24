from __future__ import annotations
import base64
import concurrent.futures
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from bridge_http import BridgeServer
from bridge_runtime import Runtime, Journal, sanitize
from bridge_security import SecurityGate
from mcp_server import State, Handler, PROFILE_CAPABILITIES, VALIDATIONS
from tests.bridge_fixture import Client


class HTTPRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'workspace'
        self.root.mkdir()
        (self.root / 'src').mkdir()
        self.state = State([self.root], token='a' * 43, profile='implement')
        self.validations = dict(VALIDATIONS)
        self.state.runtime = Runtime(self.state, Path(self.temp.name) / 'private', PROFILE_CAPABILITIES, self.validations)
        self.server = BridgeServer(('127.0.0.1', 0), Handler)
        self.server.state = self.state
        self.base = 'http://127.0.0.1:' + str(self.server.server_address[1])
        self.state.security.configure(self.base)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.02), daemon=True)
        self.thread.start()
        self.client = Client(self.base, self.state.token)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def assert_error(self, value, pattern=None):
        self.assertIn('error', value, value)
        if pattern:
            self.assertIn(pattern, value['error']['message'])
        return value

    def arguments(self, path='src/a.txt', **extra):
        return {'task_id': 'task-1', 'operation_id': 'op-1', 'path': path,
                'body': 'new', 'create': True, **extra}

    def test_control_transport_has_no_proxy_tls_or_reverse_dns_dependency(self):
        from host_control import request_json
        with (
            patch('urllib.request.build_opener', side_effect=AssertionError('proxy/TLS initialization')),
            patch('socket.getfqdn', side_effect=AssertionError('reverse DNS')),
        ):
            server = BridgeServer(('127.0.0.1', 0), Handler)
            server.server_close()
            ping = request_json(self.base + '/control', body={'operation': 'ping'},
                                headers={'X-ChatGPT-Agent-Admin': self.state.token}, local=True)
            self.assertEqual(ping['workspace_id'], self.state.workspace_id)
            for url in ['https://127.0.0.1/control', 'http://localhost/control',
                        'http://127.0.0.1.evil/control', 'http://user@127.0.0.1/control']:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    request_json(url, body={'operation': 'ping'}, local=True)

    def test_unauthenticated_and_admin_bearer_cannot_call_mcp(self):
        for token in ['missing', self.state.token]:
            status, _, body = self.client.rpc('tools/list', token=token)
            self.assertEqual(status, 401)
            self.assertNotIn(self.state.token, str(body))
        status, _, _ = self.client.request('/control', {'operation': 'pair'})
        self.assertEqual(status, 403)

    def test_real_authorization_tool_call_metadata_and_notification(self):
        self.client.authorize()
        status, _, metadata = self.client.request('/.well-known/oauth-authorization-server')
        self.assertEqual(status, 200)
        self.assertEqual(metadata['code_challenge_methods_supported'], ['S256'])
        status, _, initialized = self.client.rpc('initialize')
        self.assertEqual(initialized['id'], 7)
        self.assertEqual(initialized['result']['serverInfo']['name'], 'chatgpt-agent-mcp')
        status, _, tools = self.client.rpc('tools/list')
        self.assertTrue(tools['result']['tools'])
        for tool in tools['result']['tools']:
            self.assertEqual(tool['securitySchemes'][0]['scopes'], [self.state.security.scope])
        status, _, _ = self.client.request('/mcp', {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                                          headers={'Authorization': 'Bearer ' + self.client.access})
        self.assertEqual(status, 202)

    def test_health_response_reports_tunnel_readiness(self):
        status, _, health = self.client.request('/health')
        self.assertEqual(status, 200)
        self.assertIs(health['tunnel_ready'], True)
        self.server.tunnel_ready = False
        status, _, health = self.client.request('/health')
        self.assertEqual(status, 200)
        self.assertIs(health['tunnel_ready'], False)

    def test_persistent_bearer_oauth_redirects_without_consent_post(self):
        bearer = 'persistent-workspace-bearer'
        self.state.security = SecurityGate(
            self.state.workspace_id, PROFILE_CAPABILITIES['implement'],
            persistent_bearer=bearer)
        self.state.security.configure(self.base)
        client = Client(self.base, self.state.token).authorize_persistent()
        self.assertEqual(client.access, bearer)
        info = client.tool('workspace_info')['result']['structuredContent']
        self.assertEqual(info['workspace_id'], self.state.workspace_id)
        self.assertNotIn('session_id', info)
        self.assertNotIn('task_id', info)
        self.assertEqual(info['host_capability_ceiling'], sorted(PROFILE_CAPABILITIES['implement']))
        self.assertNotIn('capabilities', info)

    def test_persistent_bearer_requires_local_binding_then_accepts_chatgpt_dcr(self):
        bearer = 'persistent-workspace-bearer'
        self.state.security = SecurityGate(
            self.state.workspace_id, PROFILE_CAPABILITIES['implement'],
            persistent_bearer=bearer)
        self.state.security.configure(self.base)
        uri = 'https://chatgpt.com/connector/oauth/connector-smoke-01'
        status, _, registered = self.client.request('/register', {
            'client_name': 'ChatGPT', 'redirect_uris': [uri]},
            headers={'Origin': 'https://chatgpt.com'})
        self.assertEqual(status, 201)
        summaries = self.client.control('connector-clients')['clients']
        self.assertEqual(len(summaries), 1)
        self.assertFalse(summaries[0]['approved'])
        self.assertNotIn(registered['client_id'], json.dumps(summaries))
        resource = self.client.control('ping')['endpoint']
        verifier = 'c' * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        authorization = {'client_id': registered['client_id'], 'redirect_uri': uri,
                         'response_type': 'code', 'resource': resource, 'state': 'direct-bearer',
                         'code_challenge_method': 'S256', 'code_challenge': challenge}
        status, _, rejected = self.client.request(
            '/authorize?' + urlencode(authorization),
            headers={'Origin': 'https://chatgpt.com'})
        self.assertEqual(status, 400)
        self.assertEqual(rejected['error'], 'client_not_approved')
        approval = self.client.control('approve-client',
                                       client_fingerprint=summaries[0]['client_fingerprint'])
        self.assertTrue(approval['approved'])
        status, headers, _ = self.client.request(
            '/authorize?' + urlencode(authorization),
            headers={'Origin': 'https://chatgpt.com'})
        self.assertEqual(status, 302)
        code = parse_qs(urlsplit(headers['location']).query)['code'][0]
        status, _, tokens = self.client.request('/token', {
            'grant_type': 'authorization_code', 'client_id': registered['client_id'],
            'redirect_uri': uri, 'code_verifier': verifier, 'code': code,
            'resource': resource}, form=True,
            headers={'Origin': 'https://chatgpt.com'})
        self.assertEqual(status, 200)
        self.assertEqual(tokens['access_token'], bearer)

        invalid_uri = 'https://attacker.example/callback'
        status, _, untrusted = self.client.request('/register', {
            'client_name': 'Other', 'redirect_uris': [invalid_uri]})
        self.assertEqual(status, 201)
        status, _, rejected = self.client.request(
            '/authorize?' + urlencode({**authorization,
                                       'client_id': untrusted['client_id'],
                                       'redirect_uri': invalid_uri}))
        self.assertEqual(status, 400)
        self.assertEqual(rejected['error'], 'invalid_connector_redirect')

    def test_control_rejects_public_host_origin_and_malformed_frames(self):
        for headers in [{'Host': 'attacker.example'}, {'Origin': 'https://evil.example'},
                        {'Origin': self.base, 'X-ChatGPT-Agent-Admin': self.state.token}]:
            status, _, _ = self.client.request('/control', {'operation': 'pair'}, headers=headers)
            self.assertGreaterEqual(status, 400)
        self.client.authorize()
        headers = {'Authorization': 'Bearer ' + self.client.access, 'Content-Type': 'application/json'}
        for raw in [b'{"jsonrpc":"2.0","id":1,"method":"tools/list","method":"tools/call"}', b'{"id":NaN}', b'[]']:
            with self.subTest(raw=raw):
                status, _, _ = self.client.request('/mcp', raw, headers=headers, raw=True)
                self.assertEqual(status, 400)
        status, _, _ = self.client.request('/mcp', b'', raw=True, headers={**headers, 'Content-Length': '-1'})
        self.assertEqual(status, 400)

    def test_mutation_notification_and_lease_self_grant_rejected(self):
        self.client.authorize()
        self.assert_error(self.client.tool('grant-task', {'profile': 'implement'}), 'not_available')
        status, _, _ = self.client.request('/mcp', {'jsonrpc': '2.0', 'method': 'tools/call',
                    'params': {'name': 'write_text', 'arguments': self.arguments()}},
                    headers={'Authorization': 'Bearer ' + self.client.access})
        self.assertEqual(status, 400)
        self.assertFalse((self.root / 'src/a.txt').exists())

    def test_authenticated_read_only_without_task_lease(self):
        self.client.authorize()
        self.assert_error(self.client.tool('write_text', self.arguments()), 'missing_tool_arguments')
        self.assert_error(self.client.tool('run_validation', {'name': 'python-unittest', 'task_id': 'task-1',
                                                             'operation_id': 'v1'}), 'missing_tool_arguments')
        self.assertFalse((self.root / 'src/a.txt').exists())

    def test_reviewer_planner_artifact_only_and_task_namespace(self):
        self.client.authorize()
        for profile in ['review', 'plan']:
            self.client.grant(profile=profile, task=profile)
            self.assert_error(self.client.tool('write_text', self.arguments()), 'lease')
            self.assert_error(self.client.tool('run_validation', {'name': 'python-unittest', 'task_id': profile,
                                                                 'operation_id': 'v1'}), 'lease')
            value = self.client.tool('write_artifact', {'task_id': profile, 'operation_id': profile,
                                                       'name': profile + '.md', 'body': 'result'})
            self.assertIn('result', value)
            self.assertEqual((self.root / '.chatgpt-agent' / profile / (profile + '.md')).read_text(), 'result')
        self.assert_error(self.client.tool('write_artifact', {'task_id': 'task-1', 'operation_id': 'escape',
                                        'name': '../src/a.py', 'body': 'bad'}))

    def test_path_allowlist_expected_hash_and_atomic_create(self):
        self.client.authorize().grant(paths=['src/**'])
        self.assert_error(self.client.tool('write_text', self.arguments(path='outside.txt')), 'outside_task')
        created = self.client.tool('write_text', self.arguments())
        self.assertIn('result', created)
        self.assert_error(self.client.tool('write_text', self.arguments(operation_id='create-again')), 'changed')
        self.assert_error(self.client.tool('write_text', self.arguments(operation_id='stale', create=False,
                                             expected_sha256='0' * 64)), 'changed')
        value = self.client.tool('write_text', self.arguments(operation_id='replace', create=False,
                                    expected_sha256=hashlib.sha256(b'new').hexdigest(), body='replaced'))
        self.assertIn('result', value)
        self.assertEqual((self.root / 'src/a.txt').read_text(), 'replaced')

    def test_idempotency_success_is_cached_and_conflict_is_rejected(self):
        self.client.authorize().grant(paths=['src/**'])
        args = self.arguments()
        first = self.client.tool('write_text', args)
        self.assertIn('result', first)
        self.assertEqual(first, self.client.tool('write_text', args))
        self.assert_error(self.client.tool('write_text', {**args, 'body': 'different'}), 'idempotency')
        checkpoint = self.client.control('checkpoint', task_id='task-1')
        self.assertEqual(checkpoint['operations'], [{'operation_id': 'op-1', 'state': 'done'}])
        other = Journal(self.state.runtime.journal.path.parent, self.state.workspace_id)
        self.assertEqual(other.checkpoint('task-1'), checkpoint)
        payload = {'name': 'write_text',
                   'args': {key: value for key, value in args.items() if key != 'task_capability'},
                   'base_commit': None}
        self.assertEqual(other.reserve(self.client.lease_id, 'op-1', payload), first['result'])
        self.assertNotIn(self.client.task_capability.encode(), self.state.runtime.journal.path.read_bytes())

    def test_concurrent_same_operation_creates_once(self):
        self.client.authorize().grant(paths=['src/**'])
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.client.tool('write_text', self.arguments()), range(4)))
        self.assertTrue(all(value == results[0] for value in results))
        self.assertIn('result', results[0])
        self.assertEqual((self.root / 'src/a.txt').read_text(), 'new')

    def test_failed_or_interrupted_mutation_is_not_blindly_replayed(self):
        self.client.authorize().grant(paths=['src/**'])
        (self.root / 'src/a.txt').write_text('existing')
        self.assert_error(self.client.tool('write_text', self.arguments()))
        (self.root / 'src/a.txt').unlink()
        self.assert_error(self.client.tool('write_text', self.arguments()), 'outcome_unknown')
        self.assertFalse((self.root / 'src/a.txt').exists())
        self.assertEqual(self.client.control('checkpoint', task_id='task-1')['operations'][0]['state'], 'pending')

    def test_expired_revoked_wrong_session_and_other_root_cannot_write(self):
        self.client.authorize().grant(paths=['src/**'])
        other = Client(self.base, self.state.token).authorize()
        other.task_id = self.client.task_id
        other.task_capability = self.client.task_capability
        self.assert_error(other.tool('write_text', self.arguments()), 'lease')
        self.client.control('revoke-task', session_id=self.client.session, task_id='task-1')
        self.assert_error(self.client.tool('write_text', self.arguments()), 'lease')
        self.client.grant(paths=['src/**'], task='task-2', ttl=1)
        original = self.state.security.clock
        self.state.security.clock = lambda: original() + 2
        self.assert_error(self.client.tool('write_text', self.arguments(task_id='task-2')), 'lease')
        self.state.security.clock = original
        self.client.grant(paths=['src/**'], task='task-3')
        self.assert_error(self.client.tool('write_text', self.arguments(task_id='task-3', root_id='unknown')))

    def test_stale_git_base_is_rejected_without_writing(self):
        def git(*args):
            subprocess.run(['git', '-C', str(self.root), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        git('init', '-q')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '--allow-empty', '-m', 'base')
        self.client.authorize().grant(paths=['src/**'])
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '--allow-empty', '-m', 'changed')
        self.assert_error(self.client.tool('write_text', self.arguments()), 'stale_task_base')
        self.assertFalse((self.root / 'src/a.txt').exists())

    def test_invalid_types_limits_and_additional_arguments(self):
        self.client.authorize().grant(paths=['src/**'])
        for name, args in [('write_text', self.arguments(create='false')), ('read_text', {'path': 'src/a.txt', 'max_bytes': -1}),
                            ('tree', {'max_entries': True}), ('workspace_info', {'grant': 'implement'})]:
            with self.subTest(name=name, args=args):
                self.assert_error(self.client.tool(name, args))
        self.assertFalse((self.root / 'src/a.txt').exists())

    def test_shared_custom_ignore_and_link_denials_cover_reads_and_tree(self):
        (self.root / '.chatgpt-agentignore').write_text('src/private.txt\n')
        (self.root / 'src/private.txt').write_text('hidden-value')
        (self.root / '.env').write_text('hidden-value')
        self.client.authorize()
        self.assert_error(self.client.tool('read_text', {'path': 'src/private.txt'}))
        self.assertEqual(self.client.tool('search_text', {'query': 'hidden-value'})['result']['structuredContent']['results'], [])
        self.assertNotIn('private.txt', str(self.client.tool('tree', {'path': 'src'})))
        outside = Path(self.temp.name) / 'outside.txt'
        outside.write_text('outside-secret')
        os.link(outside, self.root / 'hardlink.txt')
        self.assert_error(self.client.tool('read_text', {'path': 'hardlink.txt'}))
        self.assertNotIn('hardlink.txt', str(self.client.tool('tree')))
        try:
            (self.root / 'symlink.txt').symlink_to(outside)
        except OSError:
            return  # Windows non-admin CI still covers hard links above.
        self.assert_error(self.client.tool('read_text', {'path': 'symlink.txt'}))
        self.assertEqual(self.client.tool('search_text', {'query': 'outside-secret'})['result']['structuredContent']['results'], [])

    def test_smoke_requires_real_nondiagnostic_call_and_expires_on_disconnect(self):
        self.client.authorize()
        challenge = self.client.control('begin-smoke', connector_name='test-connector')['challenge']
        status, _, _ = self.client.request('/control', {'operation': 'mark-verified', 'receipt_id': 'invented',
                                'connector_name': 'test-connector'}, headers={'X-ChatGPT-Agent-Admin': self.state.token})
        self.assertEqual(status, 403)
        diagnostic = self.client.control('probe')['access_token']
        _, _, body = self.client.rpc('tools/call', {'name': 'workspace_info', 'arguments': {'smoke_challenge': challenge}}, token=diagnostic)
        self.assert_error(body, 'invalid_smoke')
        receipt = self.client.tool('workspace_info', {'smoke_challenge': challenge})['result']['structuredContent']['smoke_receipt']
        self.client.control('mark-verified', receipt_id=receipt['receipt_id'], connector_name='test-connector')
        self.assertIsNotNone(self.client.control('receipt-status', receipt_id=receipt['receipt_id'])['receipt'])
        self.client.control('disconnect', session_id=self.client.session)
        self.assertIsNone(self.client.control('receipt-status', receipt_id=receipt['receipt_id'])['receipt'])
        self.assertEqual(self.client.rpc('tools/list')[0], 401)

    def validation(self, script, *, timeout=5):
        self.validations['python-unittest'] = [sys.executable, '-c', script]
        self.client.authorize().grant(validations=['python-unittest'])
        args = {'name': 'python-unittest', 'task_id': 'task-1', 'operation_id': 'v1', 'timeout_seconds': timeout}
        result = self.client.tool('run_validation', args)
        self.assertIn('result', result, result)
        return result['result']['structuredContent'], args

    def test_validation_has_evidence_sanitizes_stdout_stderr_and_no_reexecution(self):
        secret = 'special-env-secret-that-is-long'
        script = "import os,sys,pathlib; print('Bearer cga_at_abc123'); print('API_KEY='+os.environ['TEST_SECRET']); print(os.getcwd(), file=sys.stderr); p=pathlib.Path('count.txt'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1')"
        with patch.dict(os.environ, {'TEST_SECRET': secret}):
            result, args = self.validation(script)
        self.assertTrue(result['success'], result)
        self.assertEqual(result['exit_code'], 0)
        self.assertNotIn(secret, str(result))
        self.assertNotIn('cga_at_abc123', str(result))
        self.assertNotIn(str(self.root), result['output'])
        self.assertNotEqual(result['before']['visible_content_sha256'], result['after']['visible_content_sha256'])
        self.assertEqual(result, self.client.tool('run_validation', args)['result']['structuredContent'])
        self.assertEqual((self.root / 'count.txt').read_text(), '1')
        raw = self.state.runtime.journal.path.read_bytes()
        self.assertNotIn(secret.encode(), raw)

    def test_validation_nonzero_is_not_success(self):
        result, _ = self.validation("import sys; print('failure'); sys.exit(7)")
        self.assertFalse(result['success'])
        self.assertEqual(result['exit_code'], 7)
        self.assertEqual(result['outcome'], 'completed')

    def test_validation_timeout_and_closed_stdout_are_not_false_success(self):
        result, _ = self.validation('import os,time; os.close(1); os.close(2); time.sleep(8)', timeout=1)
        self.assertFalse(result['success'])
        self.assertEqual(result['outcome'], 'timeout')

    def test_validation_output_is_admission_bounded(self):
        result, _ = self.validation("import sys; sys.stdout.write('x'*1500000); sys.stdout.flush()")
        self.assertFalse(result['success'])
        self.assertEqual(result['outcome'], 'output_limit')
        self.assertLessEqual(len(result['output'].encode()), 40000)

    def test_validation_revocation_cancels_running_process(self):
        self.validations['python-unittest'] = [sys.executable, '-c', 'import time; print("started",flush=True); time.sleep(20)']
        self.client.authorize().grant(validations=['python-unittest'])
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(self.client.tool, 'run_validation', {'task_id': 'task-1', 'operation_id': 'v1',
                                         'name': 'python-unittest', 'timeout_seconds': 30})
            time.sleep(.25)
            self.client.control('revoke-task', session_id=self.client.session, task_id='task-1')
            result = future.result(timeout=5)['result']['structuredContent']
        self.assertEqual(result['outcome'], 'lease_revoked_or_expired')
        self.assertFalse(result['success'])

    def test_sanitization_precedes_tail_and_removes_private_key_blocks(self):
        raw = '-----BEGIN PRIVATE KEY-----\n' + 'private-material\n' * 4000 + '-----END PRIVATE KEY-----\n'
        clean = sanitize(raw)
        self.assertNotIn('private-material', clean[-40000:])
        self.assertIn('REDACTED PRIVATE KEY', clean)
