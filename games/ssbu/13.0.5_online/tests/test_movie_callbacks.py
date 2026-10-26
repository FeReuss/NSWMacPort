from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

GAME = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'VideoToolbox and libvpx require macOS')
class MovieCallbacks(unittest.TestCase):
    def test_guest_callbacks_do_not_hold_host_movie_locks(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'probe'
            build = subprocess.run([
                'xcrun', 'clang', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                '-I/opt/homebrew/opt/libvpx/include', 'tests/movie_callbacks/probe.c',
                '/opt/homebrew/opt/libvpx/lib/libvpx.a', '-framework', 'VideoToolbox',
                '-framework', 'CoreMedia', '-framework', 'CoreVideo', '-framework', 'CoreFoundation',
                '-o', binary,
            ], cwd=GAME, text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary], text=True, capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('guest callbacks run without movie locks', run.stdout)


if __name__ == '__main__':
    unittest.main()
