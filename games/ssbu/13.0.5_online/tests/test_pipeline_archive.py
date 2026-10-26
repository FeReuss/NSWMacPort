from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

GAME = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'Metal binary archives need macOS')
class PipelineArchive(unittest.TestCase):
    def test_writes_of_two_instances_take_turns(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'pipeline-archive-probe'
            build = subprocess.run(['xcrun', 'clang', '-std=gnu11', '-fobjc-arc', '-Wall', '-Wextra', '-Werror',
                                    '-Wno-unused-function', '-Ithird_party/d2r_gfx/gfx/nvn', 'tests/pipeline_archive/probe.m',
                                    '-framework', 'Foundation', '-framework', 'Metal', '-o', str(binary)], cwd=GAME,
                                   text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary], text=True, capture_output=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('pass: the archive write waited', run.stdout)


if __name__ == '__main__':
    unittest.main()
