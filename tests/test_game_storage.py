"""The shipped launcher migrates complete profiles without changing the source."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GameStorage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        source = Path(cls.build.name) / 'storage.m'
        source.write_text('#import "game_storage.h"\nint main(int argc,char **argv){@autoreleasepool{'
                          'if(argc!=4)return 2;NSError *e=nil;'
                          'NSString *p=nsw_storage_prepare(@(argv[1]),@(argv[2]),@(argv[3]),&e);'
                          'if(!p){fprintf(stderr,"%s",e.localizedDescription.UTF8String);return 1;}'
                          'puts(p.fileSystemRepresentation);return 0;}}\n')
        cls.tool = Path(cls.build.name) / 'storage'
        subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-I', ROOT / 'games/totk/app', source, '-framework', 'Foundation', '-o', cls.tool], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def prepare(self, home, game='TOTK', folder='Tears of the Kingdom', ok=True, games=None):
        env = {k: v for k, v in os.environ.items() if k != 'NSW_GAMES_DIR'}
        if games is not None:
            env['NSW_GAMES_DIR'] = str(games)
        result = subprocess.run([self.tool, home, game, folder], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stderr)
        data = (games if games is not None else home / 'Documents/NSW Games') / folder
        if ok:
            self.assertEqual(result.stdout.strip(), str(data))
        return data

    def test_shader_cache_folder_is_not_indexed_and_only_the_launcher_renames(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, tool, data = Path(tmp) / 'cache.m', Path(tmp) / 'cache', Path(tmp) / 'data'
            source.write_text('#import "game_storage.h"\nint main(int argc,char **argv){@autoreleasepool{(void)nsw_storage_prepare;if(argc!=3)return 2;'
                              'puts(nsw_shader_cache_path(@(argv[1]),"chain",atoi(argv[2])).fileSystemRepresentation);return 0;}}\n')
            subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-O2', '-Wall', '-Wextra', '-Werror', '-I', ROOT / 'games/totk/app',
                            source, '-framework', 'Foundation', '-o', tool], check=True)
            path = lambda migrate: subprocess.run([tool, data, migrate], text=True, capture_output=True, check=True).stdout.strip()
            new, old = data / 'Shaders Cache/rt-chain.noindex', data / 'Shaders Cache/rt-chain'
            self.assertEqual(path('1'), str(new))   # a new player: the folder Spotlight skips
            old.mkdir(parents=True); (old / 'a_b.metallib').write_bytes(b'lib')
            self.assertEqual(path('0'), str(old))   # the build's installer keeps an earlier folder in place
            self.assertTrue(old.is_dir())
            self.assertEqual(path('1'), str(new))   # the launcher renames it, contents kept
            self.assertEqual((new / 'a_b.metallib').read_bytes(), b'lib')
            self.assertFalse(old.exists())
            self.assertEqual(path('0'), str(new))

    def test_migrate_and_retry_preserve_identity_saves_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old = home / 'Library/Application Support/MacNativeGames/TOTK'
            files = {'profile/account.uid': b'identity', 'profile/save/uid/slot.sav': b'save',
                     'profile/pso_user.tsv': b'pipelines', 'shader-cache/rt-chain/a': b'shader',
                     'controls.txt': b'controls', 'mods/v0/m': b'mod'}
            for name, data in files.items():
                p = old / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            data = self.prepare(home)
            self.assertEqual((data / 'Saves/uid/slot.sav').read_bytes(), b'save')
            self.assertEqual((data / 'Settings/account.uid').read_bytes(), b'identity')
            self.assertEqual((data / 'Shaders Cache/rt-chain/a').read_bytes(), b'shader')
            self.assertEqual((data / 'Shaders Cache/pso_user.tsv').read_bytes(), b'pipelines')
            self.assertEqual((data / 'Settings/controls.txt').read_bytes(), b'controls')
            self.assertEqual((data / 'Mods/v0/m').read_bytes(), b'mod')
            (data / 'Saves/uid/slot.sav').write_bytes(b'new save')
            self.prepare(home)
            self.assertEqual((data / 'Saves/uid/slot.sav').read_bytes(), b'new save')
            for name, original in files.items():
                self.assertEqual((old / name).read_bytes(), original)
            self.assertFalse(list(data.parent.glob('.migration-*')))

    def test_games_are_separate_and_data_folder_can_be_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for game in ['TOTK', 'BOTW']:
                profile = home / 'Library/Application Support/MacNativeGames' / game / 'profile'
                save = profile / 'save' / game / 'slot.sav'
                save.parent.mkdir(parents=True)
                save.write_bytes(game.encode())
                (profile / 'account.uid').write_bytes(game.encode())
            totk = self.prepare(home)
            botw = self.prepare(home, 'BOTW', 'Breath of the Wild')
            self.assertNotEqual(totk, botw)
            for data, game in [(totk, 'TOTK'), (botw, 'BOTW')]:
                self.assertEqual(json.loads((data / 'game.json').read_text())['game'], game)
                self.assertEqual((data / 'Settings/account.uid').read_bytes(), game.encode())
                self.assertEqual((data / 'Saves' / game / 'slot.sav').read_bytes(), game.encode())
                self.assertEqual(list((data / 'Saves').iterdir()), [data / 'Saves' / game])
                for name in ['Saves', 'Shaders Cache', 'Screenshots', 'Settings', 'Mods', 'Logs', 'Cache']:
                    self.assertTrue((data / name).is_dir())
            self.prepare(home, 'BOTW', 'Tears of the Kingdom', ok=False)

    def test_existing_manifest_is_checked_before_creating_subdirectories(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            data = home / 'Documents/NSW Games/Tears of the Kingdom'
            data.mkdir(parents=True)
            manifest = data / 'game.json'
            for raw in [b'{"game":"BOTW"}', b'invalid JSON']:
                with self.subTest(manifest=raw):
                    manifest.write_bytes(raw)
                    self.prepare(home, ok=False)
                    self.assertEqual(list(data.iterdir()), [manifest])
                    self.assertEqual(manifest.read_bytes(), raw)

    def test_profile_controls_take_precedence_without_changing_either_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old = home / 'Library/Application Support/MacNativeGames/TOTK'
            (old / 'profile').mkdir(parents=True)
            (old / 'profile/controls.txt').write_bytes(b'current controls')
            (old / 'controls.txt').write_bytes(b'legacy controls')
            data = self.prepare(home)
            self.assertEqual((data / 'Settings/controls.txt').read_bytes(), b'current controls')
            self.assertEqual((old / 'profile/controls.txt').read_bytes(), b'current controls')
            self.assertEqual((old / 'controls.txt').read_bytes(), b'legacy controls')

    def test_override_and_copied_portable_directory_are_idempotent_on_another_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / 'first-home'
            old = home / 'Library/Application Support/MacNativeGames/TOTK/profile'
            save = old / 'save/0123456789abcdef0123456789abcdef/slot.sav'
            save.parent.mkdir(parents=True)
            save.write_bytes(b'portable save')
            (old / 'account.uid').write_bytes(b'portable identity')
            data = self.prepare(home, games=root / 'custom-games')
            self.assertFalse((home / 'Documents/NSW Games').exists())
            other = root / 'other-home'
            copied = other / 'Documents/NSW Games/Tears of the Kingdom'
            shutil.copytree(data, copied)
            other_old = other / 'Library/Application Support/MacNativeGames/TOTK/profile'
            other_old.mkdir(parents=True)
            (other_old / 'account.uid').write_bytes(b'other identity')
            (copied / 'Settings/controls.txt').write_bytes(b'portable controls')
            for _ in range(2):
                self.prepare(other)
                self.assertEqual((copied / 'Settings/account.uid').read_bytes(), b'portable identity')
                self.assertEqual((copied / 'Settings/controls.txt').read_bytes(), b'portable controls')
                self.assertEqual((copied / 'Saves/0123456789abcdef0123456789abcdef/slot.sav').read_bytes(), b'portable save')
                self.assertEqual((other_old / 'account.uid').read_bytes(), b'other identity')
            self.assertFalse(list(copied.parent.glob('.migration-*')))

    @unittest.skipIf(os.geteuid() == 0, 'root can copy unreadable files')
    def test_copy_failure_leaves_no_partial_destination_and_can_be_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old = home / 'Library/Application Support/MacNativeGames/TOTK/profile'
            old.mkdir(parents=True)
            blocked = old / 'account.uid'
            blocked.write_bytes(b'identity')
            blocked.chmod(0)
            try:
                data = self.prepare(home, ok=False)
            finally:
                blocked.chmod(0o600)
            self.assertFalse(data.exists())
            self.assertFalse(list(data.parent.glob('.migration-*')))
            self.assertEqual(blocked.read_bytes(), b'identity')
            self.prepare(home)
            self.assertEqual((data / 'Settings/account.uid').read_bytes(), b'identity')

    def test_failed_migration_leaves_source_and_no_partial_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old = home / 'Library/Application Support/MacNativeGames/TOTK/profile'
            old.parent.mkdir(parents=True)
            old.write_bytes(b'not a directory')
            data = self.prepare(home, ok=False)
            self.assertFalse(data.exists())
            self.assertEqual(old.read_bytes(), b'not a directory')
            self.assertFalse(list(data.parent.glob('.migration-*')))


if __name__ == '__main__':
    unittest.main()
