"""Real HTTP client used by bridge and subprocess CLI regressions."""
from __future__ import annotations
import base64
import hashlib
import http.client
import json
import re
from urllib.parse import urlencode, urlsplit, parse_qs


class Client:
    def __init__(self, base: str, admin: str):
        self.base, self.admin = base.rstrip('/'), admin
        self.access = None
        self.session = None
        self.task_id = None
        self.task_capability = None
        self.lease_id = None

    def request(self, path, body=None, *, method=None, headers=None, form=False, raw=False):
        parsed = urlsplit(self.base)
        headers = dict(headers or {})
        if body is not None and not raw:
            body = urlencode(body).encode() if form else json.dumps(body).encode()
            headers.setdefault('Content-Type', 'application/x-www-form-urlencoded' if form else 'application/json')
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
        try:
            connection.request(method or ('POST' if body is not None else 'GET'), path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
            if response.getheader('content-type', '').startswith('application/json'):
                data = json.loads(data)
            else:
                data = data.decode()
            return response.status, dict(response.getheaders()), data
        finally:
            connection.close()

    def control(self, operation, **body):
        status, _, value = self.request('/control', {'operation': operation, **body},
                          headers={'X-ChatGPT-Agent-Admin': self.admin})
        if status != 200:
            raise AssertionError((status, value))
        return value

    def rpc(self, method, params=None, *, token=None, rid=7):
        body = {'jsonrpc': '2.0', 'id': rid, 'method': method}
        if params is not None:
            body['params'] = params
        return self.request('/mcp', body, headers={'Authorization': 'Bearer ' + (token or self.access or '')})

    def tool(self, name, args=None):
        args = dict(args or {})
        if name in {'write_text', 'write_artifact', 'write_review', 'run_validation'} and self.task_capability:
            args.setdefault('task_id', self.task_id)
            args.setdefault('task_capability', self.task_capability)
        status, _, body = self.rpc('tools/call', {'name': name, 'arguments': args or {}})
        if status != 200:
            raise AssertionError((status, body))
        return body

    def authorize(self):
        resource = self.control('ping')['endpoint']
        uri = 'https://chatgpt.com/connector/oauth/test-client-01'
        status, _, registered = self.request('/register', {'client_name': 'ChatGPT', 'redirect_uris': [uri]})
        if status != 201:
            raise AssertionError(registered)
        self.client_id = registered['client_id']
        fingerprint = self.control('connector-clients')['clients'][-1]['client_fingerprint']
        self.control('approve-client', client_fingerprint=fingerprint)
        verifier = 'a' * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        authorization = {'client_id': self.client_id, 'redirect_uri': uri, 'response_type': 'code',
                         'resource': resource, 'state': 'regression-state',
                         'code_challenge_method': 'S256', 'code_challenge': challenge}
        status, headers, page = self.request('/authorize?' + urlencode(authorization))
        if status == 302:
            code = parse_qs(urlsplit(headers['location']).query)['code'][0]
        elif status == 200:
            nonce = re.search(r"name='consent' value='([^']+)'", page).group(1)
            pair = self.control('pair')['pairing_code']
            status, redirect, body = self.request('/authorize', {'consent': nonce,
                                         'pairing_code': pair}, form=True,
                                         headers={'Cookie': headers['set-cookie'].split(';')[0]})
            if status != 302:
                raise AssertionError((status, body))
            code = parse_qs(urlsplit(redirect['location']).query)['code'][0]
        else:
            raise AssertionError((status, page))
        exchange = {'grant_type': 'authorization_code', 'client_id': self.client_id,
                    'redirect_uri': uri, 'code_verifier': verifier, 'code': code, 'resource': resource}
        status, _, tokens = self.request('/token', exchange, form=True)
        if status != 200:
            raise AssertionError((status, tokens))
        self.access, self.refresh = tokens['access_token'], tokens.get('refresh_token')
        self.resource = resource
        self.tool('workspace_info')
        self.session = self.control('sessions')['sessions'][-1]['session_id']
        return self

    def authorize_persistent(self, redirect_uri='https://chatgpt.com/connector/oauth/test-client-01'):
        resource = self.control('ping')['endpoint']
        status, _, registered = self.request('/register', {
            'client_name': 'ChatGPT', 'redirect_uris': [redirect_uri]},
            headers={'Origin': 'https://chatgpt.com'})
        if status != 201:
            raise AssertionError(registered)
        fingerprint = self.control('connector-clients')['clients'][-1]['client_fingerprint']
        self.control('approve-client', client_fingerprint=fingerprint)
        self.client_id = registered['client_id']
        verifier = 'b' * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        authorization = {'client_id': self.client_id, 'redirect_uri': redirect_uri,
                         'response_type': 'code', 'resource': resource,
                         'state': 'persistent-state', 'code_challenge_method': 'S256',
                         'code_challenge': challenge}
        status, redirect, body = self.request(
            '/authorize?' + urlencode(authorization),
            headers={'Origin': 'https://chatgpt.com'})
        if status != 302:
            raise AssertionError((status, body))
        query = parse_qs(urlsplit(redirect['location']).query)
        if query.get('state') != ['persistent-state']:
            raise AssertionError(redirect)
        exchange = {'grant_type': 'authorization_code', 'client_id': self.client_id,
                    'redirect_uri': redirect_uri, 'code_verifier': verifier,
                    'code': query['code'][0], 'resource': resource}
        status, _, tokens = self.request('/token', exchange, form=True,
                                         headers={'Origin': 'https://chatgpt.com'})
        if status != 200:
            raise AssertionError(tokens)
        self.access, self.resource = tokens['access_token'], resource
        self.tool('workspace_info')
        self.session = self.control('sessions')['sessions'][-1]['session_id']
        return self

    def grant(self, *, profile='implement', task='task-1', paths=None, validations=None, ttl=900):
        lease = self.control('grant-task', session_id=self.session, task_id=task, profile=profile,
                             paths=paths or [], validations=validations or [], ttl=ttl)
        self.task_id = task
        self.task_capability = lease['task_capability']
        self.lease_id = lease['lease_id']
        return lease
