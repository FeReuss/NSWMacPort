"""The production thread dispatcher: BOTW preempts a guest-code spinner for a higher-priority thread; TOTK is unchanged."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

TOTK = Path(__file__).resolve().parents[1] / 'games/totk'


class CorePreemption(unittest.TestCase):
    def test_thread_probe_with_and_without_botw_preemption(self):
        with tempfile.TemporaryDirectory() as tmp:
            for flags, preempts, env, lends in (([], False, {}, False), (['-DNSW_GAME_BOTW=1'], True, {}, True),
                                                (['-DNSW_GAME_BOTW=1'], True, {'BOTW_CORE_TIMESLICE_MS': '0'}, False),
                                                (['-DNSW_GAME_BOTW=1'], True, {'BOTW_CORE_SLICE_US': '100'}, True)):
                probe = Path(tmp) / f'probe{len(flags)}'
                subprocess.run(['xcrun', 'clang', '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror', *flags,
                                '-Ithird_party/d2r_gfx/gfx/nvn/include', 'scripts/thread_probe.c', 'runtime/threads.c',
                                'runtime/thread_enter.S', '-o', probe], cwd=TOTK, check=True, capture_output=True)
                run = subprocess.run([probe], capture_output=True, text=True, timeout=60, env={**os.environ, **env})
                self.assertEqual(run.returncode, 0, run.stderr[-2000:])
                report = json.loads(run.stdout.strip().splitlines()[-1])
                self.assertEqual((report['status'], report['preemption'], report['timeslice']), ('pass', preempts, lends))
                self.assertEqual(report['stress_preemptions'] > 0, preempts)   # six pinned threads, mixed priorities: all finished


if __name__ == '__main__':
    unittest.main()
