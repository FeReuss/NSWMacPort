"""Filled vertex shaders retain the depth convention used by the translator."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1] / 'games/totk/ios/tools'
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('fill_shaders', TOOLS / 'fill_shaders.py')
fill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fill)
sys.path.remove(str(TOOLS))


class ShaderFill(unittest.TestCase):
    def test_device_cache_keeps_totk_identity(self):
        self.assertEqual(fill.chain(), 'totk-native-v4-unverified')

    def test_depth_modes_survive_indexing_and_replace_stale_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            key = 'a' * 64
            variants = [key + '-' + str(i) * 12 for i in range(3)]
            index = out / (key + '.variants')
            index.write_text(json.dumps([{'variant': variants[0], 'deps': {}}]))
            expected = []
            for variant, stage, mode in zip(variants, ['vs', 'vs', 'ps'], [True, False, True]):
                (out / (variant + '_b.json')).write_text(json.dumps({'deps': {'tex': {'0': '2d'}}}))
                row = fill.record(out, {'variant': variant, 'stage': stage, 'depth_minus_w': mode})
                self.assertEqual(row.get('depth_minus_w'), mode if stage == 'vs' else None)
                expected.append(row)
            self.assertEqual(json.loads(index.read_text()), expected)


if __name__ == '__main__':
    unittest.main()
