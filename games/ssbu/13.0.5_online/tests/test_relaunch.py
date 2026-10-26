from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

GAME = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'POSIX_SPAWN_SETEXEC is a macOS extension')
class Relaunch(unittest.TestCase):
    def test_restart_keeps_the_process_and_closes_other_descriptors(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'relaunch-probe'
            build = subprocess.run(['xcrun', 'clang', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                                    '-Iruntime', 'tests/relaunch/probe.c', '-o', str(binary)], cwd=GAME,
                                   text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary, 'arg'], text=True, capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('pass: restarted in place', run.stdout)


if __name__ == '__main__':
    unittest.main()
