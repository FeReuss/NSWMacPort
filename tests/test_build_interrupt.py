"""Stopping the CLI forwards termination so a shader child can commit its checkpoint."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BuildInterrupt(unittest.TestCase):
    def test_term_and_interrupt_allow_child_cleanup(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                child = root / 'child.py'
                child.write_text('''import os, pathlib, signal, sys, time
root = pathlib.Path(sys.argv[1])
def stop(signum, frame):
    (root / "checkpoint").write_text("committed")
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
(root / "ready").write_text(str(os.getpid()))
while True: time.sleep(.01)
''')
                wrapper = root / 'wrapper.py'
                wrapper.write_text('''import sys
from pathlib import Path
import build_app
try: build_app.run([sys.executable, sys.argv[1], sys.argv[2]], Path(sys.argv[2]) / "log")
except KeyboardInterrupt: sys.exit(130)
''')
                env = dict(os.environ, PYTHONPATH=str(ROOT))
                process = subprocess.Popen([sys.executable, wrapper, child, root], env=env,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 10
                    while not (root / 'ready').exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue((root / 'ready').exists())
                    pid = int((root / 'ready').read_text())
                    process.send_signal(sig)
                    stdout, stderr = process.communicate(timeout=15)
                    self.assertEqual(process.returncode, 130, (stdout, stderr))
                    self.assertEqual((root / 'checkpoint').read_text(), 'committed')
                    with self.assertRaises(ProcessLookupError): os.kill(pid, 0)
                finally:
                    if process.poll() is None:
                        process.kill(); process.wait()


if __name__ == '__main__':
    unittest.main()
