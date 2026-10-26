import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

GAME = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'Mach thread suspension requires macOS')
class CorePreemption(unittest.TestCase):
    def test_guest_preemption_and_host_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            for negative in (False, True):
                binary = Path(tmp) / 'probe'
                build = subprocess.run(['xcrun', 'clang', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                                '-Ithird_party/d2r_gfx/gfx/nvn/include',
                                *(['-DNEGATIVE'] if negative else []), 'tests/core_preemption/probe.c',
                                'runtime/threads.c', 'runtime/thread_enter.S', '-o', str(binary)],
                               cwd=GAME, text=True, capture_output=True)
                self.assertEqual(build.returncode, 0, build.stderr)
                env = {k: v for k, v in os.environ.items() if not k.startswith(('TOTK_', 'ODYSSEY_'))}
                run = subprocess.run([binary], env=env, text=True, capture_output=True, timeout=15)
                if negative:
                    self.assertNotEqual(run.returncode, 0)
                    self.assertIn('higher-priority waiter made progress', run.stderr)
                else:
                    self.assertEqual(run.returncode, 0, run.stderr[-2000:])
                    self.assertIn('32 guest preemptions/resumptions', run.stdout)
                    self.assertIn('persistent rotation', run.stdout)


if __name__ == '__main__':
    unittest.main()
