"""Preparation must preserve prior output and never call incomplete work playable."""
import tempfile
import unittest
from pathlib import Path
from test_game_containers import HEADER, AREA, game_image
from games.botw.prepare_code import prepare_code


class BotwPreparation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_existing_output_is_preserved_before_reading_inputs(self):
        output = self.root / 'prepared'
        output.mkdir()
        saved = output / 'keep.txt'
        saved.write_text('prior work')
        absent = self.root / 'absent'
        with self.assertRaisesRegex(ValueError, 'Output already exists'):
            prepare_code(absent, absent, absent, output)
        self.assertEqual(saved.read_text(), 'prior work')

    def test_unsigned_metadata_stops_before_rewriting_or_success_report(self):
        keys = self.root / 'prod.keys'
        keys.write_text(f'header_key = {HEADER.hex()}\nkey_area_key_application_00 = {AREA.hex()}\n')
        game = self.root / 'base.nsp'
        game.write_bytes(game_image('nsp', title=0x01007EF00011E000))
        output = self.root / 'prepared'
        with self.assertRaisesRegex(ValueError, 'invalid Nintendo NPDM signature'):
            prepare_code(game, game, keys, output)
        self.assertFalse((output / 'rewritten').exists())
        self.assertFalse((output / 'code-report.json').exists())
        self.assertFalse(list(output.rglob('*.app')))
