from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

GAME = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'uses ImageIO and CoreGraphics')
class Album(unittest.TestCase):
    def test_snapshot_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'probe'
            build = subprocess.run([
                'xcrun', 'clang', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', 'tests/album/probe.c',
                '-framework', 'CoreGraphics', '-framework', 'ImageIO', '-framework', 'CoreFoundation', '-o', binary,
            ], cwd=GAME, text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary], text=True, capture_output=True, timeout=20,
                                 env={**os.environ, 'TOTK_PROFILE_DIR': tmp})
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('album round trip ok', run.stdout)
            self.assertEqual(len(list((Path(tmp) / 'album').glob('*.jpg'))), 1)


if __name__ == '__main__':
    unittest.main()
