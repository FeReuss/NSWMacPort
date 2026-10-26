from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

GAME = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'uses the Mac network stack')
class Ldn(unittest.TestCase):
    def test_lan_access_point_and_station(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'probe'
            build = subprocess.run(['xcrun', 'clang', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', 'tests/ldn/probe.c', '-o', binary],
                                   cwd=GAME, text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary], text=True, capture_output=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('ldn LAN network ok', run.stdout)


if __name__ == '__main__':
    unittest.main()
