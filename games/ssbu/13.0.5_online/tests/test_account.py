from pathlib import Path
import subprocess
import tempfile
import unittest

GAME = Path(__file__).resolve().parents[1]


class Account(unittest.TestCase):
    def test_online_nickname_is_the_account_user_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'account-probe'
            build = subprocess.run(['xcrun', 'clang', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                                    '-Iruntime', 'tests/account/probe.c', '-o', str(binary)], cwd=GAME, text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary, tmp], text=True, capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('pass: nickname', run.stdout)


if __name__ == '__main__':
    unittest.main()
