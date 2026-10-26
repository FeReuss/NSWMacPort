from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

GAME = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'built with the Homebrew libopus the runtime links')
class OpusCodec(unittest.TestCase):
    def test_sdk8_hardware_decoder_class_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'probe'
            build = subprocess.run([
                'xcrun', 'clang', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                '-I/opt/homebrew/opt/opus/include/opus', 'tests/opus_codec/probe.c',
                '/opt/homebrew/opt/opus/lib/libopus.a', '-o', binary,
            ], cwd=GAME, text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary], text=True, capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('SDK 8 hardware Opus decoder path ok', run.stdout)


if __name__ == '__main__':
    unittest.main()
