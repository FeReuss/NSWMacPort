from pathlib import Path
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest

GAME = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'host BSD sockets as on macOS')
class NetHost(unittest.TestCase):
    def build(self, tmp):
        binary = Path(tmp) / 'probe'
        build = subprocess.run(['xcrun', 'clang', '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                                '-Wno-deprecated-declarations', 'tests/net_host/probe.c', '-framework', 'Security',
                                '-framework', 'CoreFoundation', '-o', binary], cwd=GAME, text=True, capture_output=True)
        self.assertEqual(build.returncode, 0, build.stderr)
        return binary

    def test_host_tls(self):
        """OOF_TLS=1: the game's connection runs on the host TLS stack, validated against OOF_SERVER's name with OOF_CA."""
        with tempfile.TemporaryDirectory() as tmp:
            binary = self.build(tmp)
            cert, key = Path(tmp) / 'cert.pem', Path(tmp) / 'key.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', cert, '-days', '2',
                            '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost',
                            '-addext', 'extendedKeyUsage=serverAuth'], check=True, capture_output=True)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            server = socket.create_server(('127.0.0.1', 0))
            port = server.getsockname()[1]

            def serve():
                while True:
                    try:
                        conn, _ = server.accept()
                    except OSError:
                        return
                    try:
                        with context.wrap_socket(conn, server_side=True) as tls:
                            if tls.recv(4) == b'ping':
                                tls.sendall(b'pong')
                    except (ssl.SSLError, OSError):
                        pass

            threading.Thread(target=serve, daemon=True).start()
            env = dict(os.environ, OOF_SERVER=f'localhost:{port}', OOF_TLS='1', OOF_CA=str(cert))
            run = subprocess.run([binary, 'tls'], env=env, text=True, capture_output=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('tls ok', run.stdout)
            # negative control: the system store does not trust the test certificate
            del env['OOF_CA']
            run = subprocess.run([binary, 'tls'], env=env, text=True, capture_output=True, timeout=30)
            self.assertEqual(run.returncode, 3, run.stdout + run.stderr)
            self.assertIn('not trusted', run.stderr)
            server.close()

    def test_standard_port_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = self.build(tmp)
            run = subprocess.run([binary], text=True, capture_output=True, timeout=20)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('ssbu-oof port services ok', run.stdout)
            self.assertIn('ssl Write: RMC request 21/41 call 7, 4-byte body', run.stderr)


if __name__ == '__main__':
    unittest.main()
