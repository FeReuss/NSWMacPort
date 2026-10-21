import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ConsoleSensor(unittest.TestCase):
    def test_unavailable_console_sensor_preserves_buffers_and_validates_lifecycle(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / 'console-sensor'
            result = subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-pthread',
                                     str(ROOT / 'tests/test_console_sensor.c'),
                                     str(ROOT / 'games/totk/runtime/hid_feedback.c'), '-o', str(executable)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
