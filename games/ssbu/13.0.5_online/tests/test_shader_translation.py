"""Translator behaviour on synthetic Maxwell fragment shaders (no extracted game code).

Dynamic gather offsets must be lowered before SPIR-V's constant-only operands, and a
draw's alpha test (Metal has none) must become a discard in the fragment shader.
"""
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

GAME = Path(__file__).resolve().parents[1]
XLATE = GAME / 'prebuilt/macos-arm64/totk_xlate'
CROSS = GAME / 'SSBU_build/build/spirv-cross/spirv-cross'


@unittest.skipUnless(sys.platform == 'darwin' and XLATE.is_file() and CROSS.is_file(), 'build SSBU shader tools on macOS first')
class ShaderTranslation(unittest.TestCase):
    def test_dynamic_gather_offsets(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            header = [0] * 20
            header[0] = 5 << 10  # SPH stage: fragment
            header[18] = 15  # target 0: RGBA
            # LDC.64 R4, c[3][RZ]; TLD4.PTP R0..3, (0,0), R4..5; EXIT.
            ldc = 0xef90000000000000 | (5 << 48) | (3 << 36) | (7 << 16) | (255 << 8) | 4
            gather = (0xc800000000000000 | (2 << 54) | (7 << 51) | (15 << 31)
                      | (2 << 28) | (4 << 20) | (7 << 16) | (255 << 8))
            exit_op = 0xe300000000000000 | (7 << 16) | 15
            code = out / 'synthetic.code'
            code.write_bytes(struct.pack('<20I4Q', *header, 0, ldc, gather, exit_op))
            request = dict(variant='dynamic-offsets', stage='ps', code=str(code))
            run = subprocess.run([str(XLATE), '--service', '--spirv-cross', str(CROSS),
                                  '--out', str(out), '--chain', 'ssbu-test'],
                                 input=json.dumps(request) + '\n', capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout), {'ok': True}, run.stdout)
            msl = (out / 'dynamic-offsets_b.metal').read_text()
            self.assertEqual(msl.count('.gather('), 4)
            self.assertIn('.get_width()', msl)
            self.assertIn('.get_height()', msl)
            validator = shutil.which('spirv-val')
            if validator:
                valid = subprocess.run([validator, str(out / 'dynamic-offsets.spv')],
                                       capture_output=True, text=True, timeout=10)
                self.assertEqual(valid.returncode, 0, valid.stderr)

    def test_alpha_test_discards(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            header = [0] * 20
            header[0] = 5 << 10  # SPH stage: fragment
            header[18] = 15  # target 0: RGBA, so the alpha component is written
            exit_op = 0xe300000000000000 | (7 << 16) | 15
            code = out / 'synthetic.code'
            code.write_bytes(struct.pack('<20I2Q', *header, 0, exit_op))
            requests = [dict(variant='alpha-greater', stage='ps', code=str(code), alpha=dict(func=5, ref=0.25)),
                        dict(variant='no-alpha', stage='ps', code=str(code))]
            run = subprocess.run([str(XLATE), '--service', '--spirv-cross', str(CROSS),
                                  '--out', str(out), '--chain', 'ssbu-test'],
                                 input=''.join(json.dumps(r) + '\n' for r in requests), capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual([json.loads(l) for l in run.stdout.splitlines()], [{'ok': True}] * 2, run.stdout)
            tested = (out / 'alpha-greater_b.metal').read_text()
            self.assertIn('discard_fragment()', tested)
            self.assertIn('0.25', tested)
            self.assertNotIn('discard_fragment()', (out / 'no-alpha_b.metal').read_text())   # negative control


if __name__ == '__main__':
    unittest.main()
