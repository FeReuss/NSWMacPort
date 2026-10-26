from pathlib import Path
import subprocess
import tempfile
import unittest

GAME = Path(__file__).resolve().parents[1]


class Input(unittest.TestCase):
    def test_local_multiplayer(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'probe'
            build = subprocess.run(['xcrun', 'clang', '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                                    '-Wno-unused-parameter', '-Iruntime', 'tests/input/probe.c', 'tests/input/stubs.c', '-o', binary],
                                   cwd=GAME, text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary], text=True, capture_output=True, timeout=20)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('local multiplayer ok', run.stdout)

    def test_rumble_per_player(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'rumble'
            build = subprocess.run(['xcrun', 'clang', '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                                    '-Iruntime', 'tests/input/rumble.c', '-o', binary], cwd=GAME, text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary], text=True, capture_output=True, timeout=20)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('rumble per player ok', run.stdout)


if __name__ == '__main__':
    unittest.main()
