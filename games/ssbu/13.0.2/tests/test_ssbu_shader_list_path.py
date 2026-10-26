"""shader_list.py names a list and picks its header by the game folder it is kept in, also one version folder deeper
(games/ssbu/13.0.2/assets/shader_list.tsv)."""
import sys
import unittest
from pathlib import Path

GAME = Path(__file__).resolve().parents[1]   # games/ssbu/13.0.2
sys.path.insert(0, str(GAME / 'scripts'))
import shader_list  # noqa: E402


class ListPathTest(unittest.TestCase):
    def test_version_folder(self):
        path = '/repo/games/ssbu/13.0.2/assets/shader_list.tsv'
        self.assertEqual(shader_list.list_name(path), 'games/ssbu/13.0.2/assets/shader_list.tsv')
        self.assertEqual(shader_list.header_for(path), shader_list.HEADERS['ssbu'])

    def test_game_folder_and_elsewhere(self):
        self.assertEqual(shader_list.list_name('/repo/games/totk/assets/shader_list.tsv'), 'games/totk/assets/shader_list.tsv')
        self.assertEqual(shader_list.header_for('/repo/games/totk/assets/shader_list.tsv'), shader_list.HEADER)
        self.assertEqual(shader_list.list_name('/tmp/mine.tsv'), 'mine.tsv')
        self.assertEqual(shader_list.header_for('/tmp/mine.tsv'), shader_list.HEADER)


if __name__ == '__main__':
    unittest.main()
