from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

GAME = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'mii_host.c builds on macOS')
class Mii(unittest.TestCase):
    def test_default_miis_are_three_men_and_three_women(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'mii-probe'
            build = subprocess.run(['xcrun', 'clang', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                                    '-Iruntime', 'tests/mii/probe.c', '-o', str(binary)], cwd=GAME,
                                   text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary], text=True, capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('pass: three of each', run.stdout)


if __name__ == '__main__':
    unittest.main()
