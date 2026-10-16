"""Offline tests for router sign-in: NDW4 (KeeneticOS 5.2) and the older NDW2 challenge."""
import base64
import hashlib
import hmac
import json
import os
import unittest

from services import keenetic, ndw4

SALT = os.urandom(16)
ITER, MEMCOST = 2, 1024


class Response:
    def __init__(self, status, headers=None, data=None):
        self.status_code = status
        self.headers = dict(headers or {})
        self.content = b''
        self.is_redirect = status in (301, 302, 303, 307, 308)
        values = {}
        for k, v in (headers or {}).items():
            values.setdefault(k.lower(), []).append(v)
        www = values.get('www-authenticate', [])
        if data is not None:
            self.headers['X-NDM-Data'] = base64.b64encode(json.dumps(data).encode()).decode()
        # The way urllib3 exposes repeated headers.
        self.raw = type('Raw', (), {'headers': type('H', (), {
            'getlist': staticmethod(lambda name: www if name.lower() == 'www-authenticate' else [])})()})()


class Router:
    """Router stand-in: verifies the NDW4 proof the way KeeneticOS 5.2 does."""

    def __init__(self, password, offer_ndw4=True, accept_ndw2=False, migrated=True):
        self.password, self.offer_ndw4, self.accept_ndw2 = password, offer_ndw4, accept_ndw2
        self.migrated = migrated
        self.cookies = type('Jar', (), {'clear': lambda self: None})()
        self.headers = {}
        self.logged_in = False
        self.posts = []
        self.state = {}

    def get(self, url, **kw):
        if self.logged_in:
            return Response(200)
        headers = {'X-NDM-Realm': 'Keenetic Racer', 'X-NDM-Challenge': 'CHAL'}
        if self.offer_ndw4:
            headers['WWW-Authenticate'] = 'x-ndw4-interactive endpoint="/auth/ndw4"'
        return Response(401, headers)

    def post(self, url, json=None, **kw):
        self.posts.append((url, dict(json)))
        if url.endswith('/auth'):  # NDW2
            md5 = hashlib.md5(f"{json['login']}:Keenetic Racer:{self.password}".encode()).hexdigest()
            ok = self.accept_ndw2 and json['password'] == hashlib.sha256(f'CHAL{md5}'.encode()).hexdigest()
            self.logged_in = ok
            return Response(200 if ok else 401)
        if 'proof' not in json and 'signature-proof' not in json:
            if not self.migrated:  # password still stored the pre-5.2 way
                return Response(401)
            nonce = json['nonce'] + base64.b64encode(os.urandom(12)).decode()
            self.state = {'nonce1': json['nonce'], 'nonce': nonce}
            return Response(401, data={'salt': base64.b64encode(SALT).decode(), 'nonce': nonce,
                                       'iter': ITER, 'memcost': MEMCOST})
        st = self.state
        message = (f"login1=admin,nonce1={st['nonce1']};iter2={ITER},memcost2={MEMCOST},"
                   f"nonce2={st['nonce']},salt2={base64.b64encode(SALT).decode()};"
                   f"login3=admin,nonce3={st['nonce']}")
        client_key, stored_key, server_key = ndw4.derive_keys(self.password.encode(), SALT, ITER, MEMCOST)
        if 'proof' in json:
            st['ok'] = json['proof'] == ndw4.proof(client_key, stored_key, message)
            signature = base64.b64encode(hmac.new(server_key, message.encode(), hashlib.sha3_512).digest()).decode()
            st['signature'] = signature
            return Response(401, data={'signature': signature})
        expected = ndw4.proof(client_key, stored_key, f"{message};signature4={st['signature']}")
        self.logged_in = st.get('ok') and json['signature-proof'] == expected
        return Response(200 if self.logged_in else 401)


def client(router, password):
    c = keenetic.KeeneticClient('192.168.1.1', 'admin', password)
    c._session = router
    return c


class SignInTest(unittest.TestCase):
    def test_ndw4_correct_password(self):
        router = Router('Secret-1')
        client(router, 'Secret-1').authenticate()
        self.assertTrue(router.logged_in)
        self.assertFalse(any(url.endswith('/auth') for url, _ in router.posts))  # NDW2 not tried

    def test_ndw4_wrong_password_is_final(self):
        router = Router('Secret-1', accept_ndw2=True)
        c = client(router, 'wrong')
        with self.assertRaises(keenetic.KeeneticAuthError):
            c.authenticate()
        attempts = len(router.posts)
        with self.assertRaises(keenetic.KeeneticAuthError):
            c.authenticate()
        self.assertEqual(attempts, len(router.posts))  # no second attempt
        self.assertFalse(any(url.endswith('/auth') for url, _ in router.posts))

    def test_ndw2_when_password_not_migrated(self):
        router = Router('Secret-1', accept_ndw2=True, migrated=False)
        client(router, 'Secret-1').authenticate()
        self.assertTrue(router.logged_in)
        self.assertEqual(1, sum(url.endswith('/auth/ndw4') for url, _ in router.posts))

    def test_ndw2_on_older_firmware(self):
        router = Router('Secret-1', offer_ndw4=False, accept_ndw2=True)
        client(router, 'Secret-1').authenticate()
        self.assertTrue(router.logged_in)


if __name__ == '__main__':
    unittest.main()
