from pathlib import Path
import subprocess
import tempfile
import unittest

GAME = Path(__file__).resolve().parents[1]


class Friends(unittest.TestCase):
    def test_server_list_becomes_sdk_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'probe'
            build = subprocess.run(['xcrun', 'clang', '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                                    '-Iruntime', 'tests/friends/probe.c', '-o', binary], cwd=GAME, text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary], text=True, capture_output=True, timeout=20)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('friends ok', run.stdout)


if __name__ == '__main__':
    unittest.main()
