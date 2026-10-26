from pathlib import Path
import subprocess
import tempfile
import unittest

GAME = Path(__file__).resolve().parents[1]


class Launcher(unittest.TestCase):
    def test_server_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'probe'
            # a server's own CA (self-signed, localhost), as an ssbu-oof operator would publish it
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', Path(tmp) / 'ca-key.pem',
                            '-out', Path(tmp) / 'ca.pem', '-days', '2', '-subj', '/CN=localhost',
                            '-addext', 'subjectAltName=DNS:localhost', '-addext', 'extendedKeyUsage=serverAuth'],
                           check=True, capture_output=True)   # macOS refuses a TLS server certificate without serverAuth
            build = subprocess.run(['xcrun', 'clang', '-O1', '-Wall', '-Wextra', '-Werror', '-fobjc-arc', '-framework', 'Cocoa',
                                    '-framework', 'Security',
                                    '-DSHADER_CHAIN="rt-test"', 'tests/launcher/probe.m', '-o', binary],
                                   cwd=GAME, text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary, tmp], text=True, capture_output=True, timeout=20)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('server list ok', run.stdout)


if __name__ == '__main__':
    unittest.main()
