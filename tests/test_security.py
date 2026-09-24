from __future__ import annotations
import base64
import hashlib
import unittest
from bridge_security import SecurityGate, Denied, digest, https_base, redirect_uri
from mcp_server import PROFILE_CAPABILITIES


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.now = 10000.
        self.gate = SecurityGate('ws_test', PROFILE_CAPABILITIES['implement'], clock=lambda: self.now)
        self.gate.configure('https://bridge.example')
        self.uri = 'https://chatgpt.example/callback'
        self.client = self.gate.register({'redirect_uris': [self.uri]})['client_id']
        self.verifier = 'a' * 64
        self.challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip('=')

    def form(self, **overrides):
        return {'client_id': self.client, 'redirect_uri': self.uri, 'response_type': 'code',
                'state': 'state', 'resource': self.gate.resource, 'code_challenge': self.challenge,
                'code_challenge_method': 'S256', **overrides}

    def code(self):
        nonce, _ = self.gate.begin_authorization(self.form())
        pairing = self.gate.new_pairing()['pairing_code']
        return self.gate.approve(nonce, pairing)[0]

    def exchange(self, code, **overrides):
        return self.gate.exchange({'grant_type': 'authorization_code', 'client_id': self.client,
              'redirect_uri': self.uri, 'code_verifier': self.verifier, 'code': code,
              'resource': self.gate.resource, **overrides})

    def test_urls_require_https_or_exact_loopback_callback(self):
        for uri in ['http://example.com', 'https://x@evil.com', 'https://x/#frag', 'https://x/\\evil']:
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                https_base(uri)
        for uri in ['http://localhost.evil/cb', 'javascript:alert(1)', 'https://x/cb#frag']:
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                redirect_uri(uri)
        self.assertEqual(redirect_uri('http://127.0.0.1:99/cb'), 'http://127.0.0.1:99/cb')

    def test_exact_redirect_client_resource_scope_and_pkce_required(self):
        for overrides in [{'redirect_uri': self.uri + '/other'}, {'client_id': 'other'}, {'resource': 'https://other/mcp'},
                          {'scope': 'admin'}, {'code_challenge_method': 'plain'}, {'code_challenge': 'short'}]:
            with self.subTest(overrides=overrides), self.assertRaises(Denied):
                self.gate.begin_authorization(self.form(**overrides))

    def test_consent_pairing_is_single_use_and_locked_after_attempts(self):
        nonce, _ = self.gate.begin_authorization(self.form())
        pair = self.gate.new_pairing()['pairing_code']
        self.assertNotIn(pair, repr(self.gate.pairing))
        for _ in range(5):
            with self.assertRaises(Denied):
                self.gate.approve(nonce, 'wrong')
        with self.assertRaisesRegex(Denied, 'locked'):
            self.gate.approve(nonce, pair)
        self.assertIsNone(self.gate.pairing)
        pair = self.gate.new_pairing()['pairing_code']
        code, _ = self.gate.approve(nonce, pair)
        self.assertEqual(self.gate.approve(nonce, pair)[0], code)
        self.assertIn(digest(code), self.gate.codes)
        self.assertNotIn(code, self.gate.codes)

    def test_expired_pairing_and_code_rejected(self):
        nonce, _ = self.gate.begin_authorization(self.form())
        pair = self.gate.new_pairing()['pairing_code']
        self.now += 301
        with self.assertRaises(Denied):
            self.gate.approve(nonce, pair)
        code = self.code()
        self.now += 301
        with self.assertRaises(Denied):
            self.exchange(code)

    def test_exchange_wrong_verifier_consumes_code(self):
        code = self.code()
        with self.assertRaises(Denied):
            self.exchange(code, code_verifier='b' * 64)
        with self.assertRaises(Denied):
            self.exchange(code)

    def test_wrong_client_or_redirect_does_not_receive_token(self):
        for overrides in [{'client_id': 'other'}, {'redirect_uri': self.uri + '/other'}]:
            with self.subTest(overrides=overrides), self.assertRaises(Denied):
                self.exchange(self.code(), **overrides)

    def test_persistent_bearer_requires_local_binding_then_authorizes_with_pkce(self):
        bearer = 'persistent-workspace-bearer'
        gate = SecurityGate('ws_test', PROFILE_CAPABILITIES['implement'],
                            clock=lambda: self.now, persistent_bearer=bearer)
        gate.configure('https://bridge.example')
        callback = 'https://chatgpt.com/connector/oauth/test-client-01'
        client = gate.register({'client_name': 'ChatGPT', 'redirect_uris': [callback]})['client_id']
        summaries = gate.registered_client_summaries()
        self.assertEqual(len(summaries), 1)
        self.assertFalse(summaries[0]['approved'])
        self.assertNotIn(client, repr(summaries))
        gate_form = {'client_id': client, 'redirect_uri': callback, 'response_type': 'code',
                     'state': 'state', 'resource': gate.resource,
                     'code_challenge': self.challenge, 'code_challenge_method': 'S256'}
        with self.assertRaisesRegex(Denied, 'client_not_approved'):
            gate.begin_authorization(gate_form)
        approved = gate.approve_registered_client(summaries[0]['client_fingerprint'])
        self.assertTrue(approved['approved'])
        nonce, _ = gate.begin_authorization(gate_form)
        code, _ = gate.approve(nonce)
        tokens = gate.exchange({'grant_type': 'authorization_code', 'client_id': client,
                                'redirect_uri': callback, 'code_verifier': self.verifier,
                                'code': code, 'resource': gate.resource})
        self.assertEqual(tokens['access_token'], bearer)
        self.assertIsNone(gate.pairing)

        other = gate.register({'client_name': 'Other',
                               'redirect_uris': ['https://attacker.example/callback']})['client_id']
        with self.assertRaisesRegex(Denied, 'invalid_connector_redirect'):
            gate.begin_authorization({**gate_form, 'client_id': other,
                                      'redirect_uri': 'https://attacker.example/callback'})

    def test_code_replay_and_workspace_audience(self):
        code = self.code()
        tokens = self.exchange(code)
        with self.assertRaises(Denied):
            self.exchange(code)
        self.assertNotIn(tokens['access_token'], self.gate.access)
        other = SecurityGate('ws_other', PROFILE_CAPABILITIES['implement'])
        with self.assertRaises(Denied):
            other.authenticate(tokens['access_token'])

    def test_refresh_rotation_and_reuse_revoke_family(self):
        tokens = self.exchange(self.code())
        sid = self.gate.authenticate(tokens['access_token'])
        self.gate.grant(sid, 'task', 'review', PROFILE_CAPABILITIES['review'], (), (), None, 900)
        form = {'grant_type': 'refresh_token', 'client_id': self.client,
                'refresh_token': tokens['refresh_token'], 'resource': self.gate.resource}
        refreshed = self.gate.exchange(form)
        self.assertEqual(self.gate.authenticate(refreshed['access_token']), sid)
        with self.assertRaises(Denied):
            self.gate.authenticate(tokens['access_token'])
        with self.assertRaisesRegex(Denied, 'reuse'):
            self.gate.exchange(form)
        with self.assertRaises(Denied):
            self.gate.authenticate(refreshed['access_token'])
        self.assertFalse(any(lease.session_id == sid for lease in self.gate.leases.values()))

    def test_access_expiration_and_revocation(self):
        tokens = self.exchange(self.code())
        self.now += 901
        with self.assertRaises(Denied):
            self.gate.authenticate(tokens['access_token'])
        self.gate.revoke_token(tokens['refresh_token'], self.client)
        self.assertFalse(self.gate.sessions)

    def test_no_diagnostic_lease_or_smoke_receipt(self):
        token = self.gate.diagnostic_token()['access_token']
        sid = self.gate.authenticate(token)
        with self.assertRaises(Denied):
            self.gate.grant(sid, 'task', 'review', PROFILE_CAPABILITIES['review'], (), (), None, 60)
        challenge = self.gate.begin_smoke('connector')['challenge']
        with self.assertRaises(Denied):
            self.gate.observe_smoke(sid, challenge)

    def test_lease_role_ceiling_expiry_and_renewal(self):
        sid = self.gate.authenticate(self.exchange(self.code())['access_token'])
        lease, cap = self.gate.grant(sid, 'task', 'review', PROFILE_CAPABILITIES['review'], (), (), None, 10)
        with self.assertRaises(Denied):
            self.gate.require(sid, 'task', 'workspace.write', cap)
        with self.assertRaises(Denied):
            self.gate.require(sid, 'other', 'artifact.write', cap)
        with self.assertRaises(Denied):
            self.gate.require(sid, 'task', 'artifact.write', 'wrong-capability')
        self.now += 11
        self.assertFalse(self.gate.active(lease))
        with self.assertRaises(Denied):
            self.gate.require(sid, 'task', 'artifact.write', cap)
        self.gate.maximum = PROFILE_CAPABILITIES['review']
        with self.assertRaisesRegex(Denied, 'ceiling'):
            self.gate.grant(sid, 'task', 'implement', PROFILE_CAPABILITIES['implement'], (), (), None, 60)

    def test_task_leases_are_isolated_by_task_and_capability(self):
        sid = self.gate.authenticate(self.exchange(self.code())['access_token'])
        lease_a, cap_a = self.gate.grant(sid, 'task-a', 'implement',
            PROFILE_CAPABILITIES['implement'], ('src/**',), (), None, 60)
        lease_b, cap_b = self.gate.grant(sid, 'task-b', 'review',
            PROFILE_CAPABILITIES['review'], (), (), None, 60)
        self.assertNotEqual(lease_a.lease_id, lease_b.lease_id)
        self.assertEqual(self.gate.require(sid, 'task-a', 'workspace.write', cap_a), lease_a)
        self.assertEqual(self.gate.require(sid, 'task-b', 'artifact.write', cap_b), lease_b)
        with self.assertRaises(Denied):
            self.gate.require(sid, 'task-b', 'artifact.write', cap_a)

    def test_smoke_receipt_is_server_observed_bound_and_revocable(self):
        sid = self.gate.authenticate(self.exchange(self.code())['access_token'])
        with self.assertRaises(Denied):
            self.gate.verify_receipt('invented', 'connector')
        challenge = self.gate.begin_smoke('connector')['challenge']
        receipt = self.gate.observe_smoke(sid, challenge)
        self.assertIsNone(self.gate.receipt_status(receipt['receipt_id']))
        with self.assertRaises(Denied):
            self.gate.verify_receipt(receipt['receipt_id'], 'other')
        self.gate.verify_receipt(receipt['receipt_id'], 'connector')
        self.assertEqual(self.gate.receipt_status(receipt['receipt_id'])['boot_id'], self.gate.boot_id)
        with self.assertRaises(Denied):
            self.gate.observe_smoke(sid, challenge)
        self.gate.revoke_session(sid)
        self.assertIsNone(self.gate.receipt_status(receipt['receipt_id']))

    def test_auth_work_is_rate_and_memory_bounded(self):
        for _ in range(120):
            self.gate.throttle()
        with self.assertRaisesRegex(Denied, 'rate_limited'):
            self.gate.throttle()
        self.now += 60
        self.gate.throttle()
        for _ in range(127):
            self.gate.register({'redirect_uris': [self.uri]})
        with self.assertRaisesRegex(Denied, 'capacity'):
            self.gate.register({'redirect_uris': [self.uri]})
