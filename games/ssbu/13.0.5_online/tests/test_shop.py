from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

GAME = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'applet_host.c builds on macOS')
class Shop(unittest.TestCase):
    def test_shop_pages_close_and_the_shop_service_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'shop-probe'
            build = subprocess.run(['xcrun', 'clang', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                                    '-Iruntime', 'tests/shop/probe.c', '-o', str(binary)], cwd=GAME,
                                   text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary], text=True, capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('pass: no shop', run.stdout)


if __name__ == '__main__':
    unittest.main()
