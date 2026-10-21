"""nsw_quickstart.py: finding a player's files by title ID and the build command it runs."""
from pathlib import Path
import importlib.util
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('nsw_quickstart', ROOT / 'nsw_quickstart.py')
quick = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quick)


class QuickStart(unittest.TestCase):
    def test_discover_classifies_a_dump_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = ['games/Zelda BOTW [01007EF00011E000][v0].nsp', 'games/Zelda BOTW [01007EF00011E800][v1114112] Update.nsp',
                     'games/DLC/Pack 2 [01007EF00011F002][v196608].nsp', 'games/DLC/Pack 1 [01007EF00011F001][v196608].nsp',
                     'games/Zelda TOTK [0100F2C0115B6000].xci', 'keys/prod.keys', 'games/BOTW_build/old [01007EF00011E000].nsp']
            for name in names:
                (root / name).parent.mkdir(parents=True, exist_ok=True); (root / name).write_bytes(b'x')
            (root / 'fw').mkdir()
            for i in range(60):
                (root / f'fw/{i:02x}.nca').write_bytes(b'x')
            found = quick.discover(root, 'botw')
            self.assertEqual(found['base'].name, 'Zelda BOTW [01007EF00011E000][v0].nsp')   # not the copy in BOTW_build
            self.assertIn('01007EF00011E800', found['update'].name)
            self.assertEqual([p.name[:6] for p in found['dlc']], ['Pack 1', 'Pack 2'])
            self.assertEqual(found['keys'], root / 'keys/prod.keys')
            self.assertEqual(found['firmware'], root / 'fw')
            totk = quick.discover(root, 'totk')
            self.assertEqual(totk['base'].suffix, '.xci')
            self.assertIsNone(totk['update']); self.assertEqual(totk['dlc'], [])

    def test_build_command_and_dragged_paths(self):
        self.assertEqual(quick.clean_path(r"'/Volumes/My Games/botw.nsp' "), Path('/Volumes/My Games/botw.nsp'))
        self.assertEqual(quick.clean_path(r'/Volumes/My\ Games/botw.nsp'), Path('/Volumes/My Games/botw.nsp'))
        self.assertIsNone(quick.clean_path('  '))
        cmd = quick.build_command('py', 'botw', Path('/g/b.nsp'), Path('/k/prod.keys'), Path('/g/u.nsp'),
                                  [Path('/g/d1.nsp'), Path('/g/d2.nsp')], Path('/fw'), Path('/Apps/B.app'))
        self.assertEqual(cmd[1:], [str(ROOT / 'build_app.py'), '--game', 'botw', '/g/b.nsp', '-k', '/k/prod.keys', '--update', '/g/u.nsp',
                                   '--dlc', '/g/d1.nsp', '--dlc', '/g/d2.nsp', '--firmware', '/fw', '-o', '/Apps/B.app'])
        shaders = quick.build_command('py', 'botw', Path('/g/b.nsp'), Path('/k/prod.keys'), Path('/g/u.nsp'), shaders_only=True)
        self.assertEqual(shaders[1], str(ROOT / 'build_shaders.py'))
        self.assertNotIn('-o', shaders); self.assertNotIn('--firmware', shaders)


if __name__ == '__main__':
    unittest.main()
