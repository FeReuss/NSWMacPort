from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

GAME = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'native Foundation cache lookup requires macOS')
class ShaderCache(unittest.TestCase):
    def test_completed_variants_are_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'shader-cache-probe'
            build = subprocess.run(['xcrun', 'clang', '-std=gnu11', '-fobjc-arc', '-Wall', '-Wextra', '-Werror',
                                    '-Wno-unused-function', '-Wno-deprecated-declarations',
                                    '-Ithird_party/d2r_gfx/gfx/nvn/include', 'tests/shader_cache/probe.m',
                                    '-framework', 'Foundation', '-o', str(binary)], cwd=GAME,
                                   text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary], text=True, capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('100 cache hits without retranslation', run.stdout)


if __name__ == '__main__':
    unittest.main()
