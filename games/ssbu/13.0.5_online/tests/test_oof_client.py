import contextlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import ssl
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

GAME = Path(__file__).resolve().parents[1]
VALID = {'id_token': 'oof1.first', 'expires_in': 3600}


@contextlib.contextmanager
def token_server(responses, cert=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, None))
            self.answer()

        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            if self.path == '/v1/friends':   # fetched after every token (oof_client.m); not one of the token answers
                return self.answer((200, {'friends': [], 'incoming': [], 'outgoing': [], 'blocked': [], 'max': 300}))
            requests.append((self.path, json.loads(body)))
            self.answer()

        def answer(self, reply=None):
            status, response = reply or responses[len(requests) - 1]
            data = response if isinstance(response, bytes) else json.dumps(response).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    with HTTPServer(('127.0.0.1', 0), Handler) as server:
        if cert:   # HTTPS with a test certificate (cert = (certificate, key))
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(*cert)
            server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'127.0.0.1:{server.server_port}', requests
        finally:
            server.shutdown()
            thread.join()


@unittest.skipUnless(sys.platform == 'darwin', 'Foundation client requires macOS')
class OofClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.binary = Path(cls.tmp.name) / 'probe'
        net_host = Path(cls.tmp.name) / 'net_host.o'   # runtime_oof_trust_ok, shared with the game's TLS
        for command in (['xcrun', 'clang', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-deprecated-declarations',
                         '-c', 'runtime/net_host.c', '-o', net_host],
                        ['xcrun', 'clang', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-fobjc-arc', '-framework', 'Foundation',
                         '-framework', 'Security', 'tests/oof_client/probe.m', net_host, '-o', cls.binary]):
            build = subprocess.run(command, cwd=GAME, text=True, capture_output=True)
            if build.returncode:
                raise AssertionError(build.stderr)

    def run_probe(self, *, config=False, expire=False, nat=False, **settings):
        env = {k: v for k, v in os.environ.items() if not k.startswith('OOF_')}
        env.update(OOF_SERVER='127.0.0.1:1', OOF_PID='4294967297', OOF_DEVICE_KEY='00' * 32, OOF_TLS='0')
        for key, value in settings.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        run = subprocess.run([self.binary, 'config' if config else 'expire' if expire else 'nat' if nat else 'refresh'],
                             env=env, text=True, capture_output=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        return [line.split(' ', 2) for line in run.stdout.splitlines()]

    def test_every_ensure_fetches_and_loads_a_fresh_token(self):
        second = {'id_token': 'oof1.second', 'expires_in': 3600}
        with token_server([(200, VALID), (200, second)]) as (server, requests):
            rows = self.run_probe(OOF_SERVER=server, expire=True)
        self.assertEqual(rows, [['0', '10', 'oof1.first'], ['0', '11', 'oof1.second']])
        self.assertEqual(requests, [('/v1/token', {'pid': 4294967297, 'device_key': '00' * 32})] * 2)

    def test_custom_ca_is_trusted_for_the_server(self):
        """OOF_CA (standard section 1): an HTTPS server with a certificate the system does not trust."""
        cert, key = Path(self.tmp.name) / 'cert.pem', Path(self.tmp.name) / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', cert, '-days', '2',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost',
                        '-addext', 'extendedKeyUsage=serverAuth'], check=True, capture_output=True)
        with token_server([(200, VALID)], cert=(cert, key)) as (server, requests):
            rows = self.run_probe(OOF_SERVER=server.replace('127.0.0.1', 'localhost'), OOF_TLS='1', OOF_CA=str(cert))
        self.assertEqual(rows[0], ['0', '10', 'oof1.first'])
        # negative control: without OOF_CA the system store refuses the certificate and no token is fetched
        with token_server([(200, VALID)], cert=(cert, key)) as (server, requests):
            rows = self.run_probe(OOF_SERVER=server.replace('127.0.0.1', 'localhost'), OOF_TLS='1')
        self.assertNotEqual(rows[0][0], '0')

    def test_nat_check_addresses_come_from_status_once(self):
        status = {'server': 'ssbu-oof', 'nat_check': {'primary': '10.0.0.1', 'secondary': '10.0.0.2'}}
        with token_server([(200, status)]) as (server, requests):
            rows = self.run_probe(OOF_SERVER=server, nat=True)
        self.assertEqual(rows, [['1', '10.0.0.1', '10.0.0.2']] * 2)
        self.assertEqual(requests, [('/v1/status', None)])
        for nat in (None, {'primary': '10.0.0.1', 'secondary': '10.0.0.1'}, {'primary': '10.0.0.1'},
                    {'primary': '0.0.0.0', 'secondary': '10.0.0.2'}, {'primary': 'host', 'secondary': '10.0.0.2'},
                    {'primary': 1, 'secondary': 2}, []):
            with self.subTest(nat=nat):
                with token_server([(200, {'server': 'ssbu-oof', 'nat_check': nat})]) as (server, requests):
                    rows = self.run_probe(OOF_SERVER=server, nat=True)
                self.assertEqual([r[0] for r in rows], ['0', '0'])
                self.assertEqual(len(requests), 1)

    def test_failed_refresh_discards_the_old_token(self):
        invalid = [
            (401, {'error': 'unauthorized'}), (200, b'not json'), (200, []),
            (200, {'id_token': '', 'expires_in': 3600}),
            (200, {'id_token': 'oof1.\u2603', 'expires_in': 3600}),
            (200, {'id_token': 'oof1.\0hidden', 'expires_in': 3600}),
            (200, {'id_token': 'x' * 4096, 'expires_in': 3600}),
        ] + [(200, {'id_token': 'oof1.bad', 'expires_in': ttl})
             for ttl in (0, -1, 0.5, True, '3600', 31536001, 10**20)]
        for response in invalid:
            with self.subTest(response=str(response)[:100]):
                with token_server([(200, VALID), response]) as (server, requests):
                    rows = self.run_probe(OOF_SERVER=server)
                self.assertEqual(rows[0], ['0', '10', 'oof1.first'])
                self.assertNotEqual(rows[1][0], '0')
                self.assertEqual(rows[1][1:], ['0', ''])
                self.assertEqual(len(requests), 2)

    def test_invalid_endpoints_stay_offline_without_throwing(self):
        for server in ('', 'localhost', 'localhost:0', 'localhost:-1', 'localhost:65536',
                       'localhost:abc', 'localhost:99999999999999999999999999', '[::1:443',
                       'user@localhost:443', 'localhost:443/path', 'localhost:443?x',
                       'localhost:443#x', 'local host:443', 'localhost%2e:443'):
            with self.subTest(server=server):
                self.assertEqual(self.run_probe(config=True, OOF_SERVER=server)[0][0], '0')
                self.assertEqual(self.run_probe(OOF_SERVER=server), [['1', '0', '']] * 2)

    def test_pid_is_complete_decimal_below_2_to_53(self):
        for pid in ('1', str(2**53 - 1)):
            with self.subTest(pid=pid):
                self.assertEqual(self.run_probe(config=True, OOF_PID=pid)[0][:2], ['1', pid])
        for pid in ('', '0', '-1', '+1', ' 1', '1 ', '1x', '1.0', '1e3', str(2**53), str(2**64)):
            with self.subTest(pid=pid):
                self.assertEqual(self.run_probe(config=True, OOF_PID=pid)[0][:2], ['0', '0'])

    def test_tls_defaults_on_and_invalid_switch_is_rejected(self):
        self.assertEqual(self.run_probe(config=True, OOF_TLS=None)[0], ['1', '4294967297', 'https'])
        self.assertEqual(self.run_probe(config=True, OOF_TLS='1')[0][2], 'https')
        self.assertEqual(self.run_probe(config=True, OOF_TLS='0')[0][2], 'http')
        self.assertEqual(self.run_probe(config=True, OOF_TLS='true')[0][0], '0')
        self.assertEqual(self.run_probe(config=True, OOF_SERVER='[::1]:443')[0][0], '1')


if __name__ == '__main__':
    unittest.main()
