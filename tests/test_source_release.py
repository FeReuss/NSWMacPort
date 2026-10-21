"""The source export excludes local game data and preserves build sources."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('source_release', ROOT / 'tools/build_source_release.py')
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class SourceRelease(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'checkout'
        self.repo.mkdir()
        subprocess.run(['git', 'init', '-q', self.repo], check=True)

    def put(self, name, data=b'source\n'):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_sources_private_paths_and_reproducible_archive(self):
        wanted = {
            '.gitignore', 'build_app.py', 'build_shaders.py', 'README.md', 'LICENSE', 'LICENSE.upstream-MIT', 'THIRD_PARTY_NOTICES.md', 'NOTICE',
            'nsw_quickstart.py',
            'nsw_profile.py', 'tools/build_source_release.py', 'docs/how-it-works.md', 'games/botw/prepare_code.py',
            'games/totk/scripts/verify_rewrites.py', 'games/totk/runtime/gfx_probe.c',
            'games/totk/Makefile', 'games/totk/assets/expected-v1.0.0.json',
            'games/totk/assets/shader_list.tsv', 'games/totk/third_party/d2r/LICENSE',
            'games/totk/third_party/d2r_gfx/gfx/tex/nvn_bctc.metal', 'vendor/ryujinx/LICENSE.txt',
        }
        for name in wanted:
            self.put(name)
        # Tracked files must use their current contents too.
        subprocess.run(['git', '-C', self.repo, 'add', 'build_app.py'], check=True)
        self.put('build_app.py', b'changed local source\n')
        for name in ('BOTW_build/native/code.py', 'games/totk/tests/probe.c', 'tests/test.c',
                     'games/totk/build/private.h', 'games/totk/analysis/log.txt',
                     'games/totk/extracted/game.txt', 'games/totk/tooling/private.py',
                     'games/totk/romfs/data.txt', 'games/totk/cache/shader.txt',
                     'tools/obj/output.txt', 'tools/.DS_Store', 'tools/__pycache__/file.pyc',
                     'games/totk/assets/prod.keys', 'games/totk/runtime/run.log',
                     'games/totk/assets/test.nsp', 'games/totk/assets/font.bfttf'):
            self.put(name)
        outside = Path(self.temp.name) / 'outside'
        self.put('games/totk/assets/' + 'a' * 64 + '-bbbbbbbbbbbb_b.metal', b'game-derived shader text')
        outside.mkdir()
        (outside / 'private.py').write_text('private source')
        (self.repo / 'games/link').symlink_to(outside, target_is_directory=True)
        (self.repo / 'tools/link.py').symlink_to(outside / 'private.py')
        first, second = Path(self.temp.name) / 'first.tar.gz', Path(self.temp.name) / 'second.tar.gz'
        release.build_release(self.repo, first)
        release.build_release(self.repo, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first) as archive:
            names = {item.name.removeprefix(release.PREFIX + '/') for item in archive.getmembers()}
            self.assertEqual(names, wanted | {'SOURCE_MANIFEST.sha256'})
            self.assertEqual(archive.extractfile(release.PREFIX + '/build_app.py').read(), b'changed local source\n')
            manifest = archive.extractfile(release.PREFIX + '/SOURCE_MANIFEST.sha256').read().decode()
            self.assertIn(hashlib.sha256(b'changed local source\n').hexdigest() + '  build_app.py\n', manifest)
            self.assertTrue(all(item.isfile() and item.mtime == 0 and item.uid == 0 for item in archive.getmembers()))
        with self.assertRaises(FileExistsError):
            release.build_release(self.repo, first)
        self.assertEqual((outside / 'private.py').read_text(), 'private source')

    def test_rejects_unknown_binary_nintendo_data_and_keys(self):
        candidate = self.put('games/totk/assets/unknown.txt')
        for data in (b'\x00\xffbinary', b'NSO0hidden game', b'PFS0container',
                     b' ' * 0x100 + b'HEAD', b' ' * 0x200 + b'NCA3',
                     b'header_key = ' + b'a' * 64 + b'\n',
                     b'a' * 32 + b' = ' + b'b' * 32 + b'\n'):
            with self.subTest(data=data[:8]):
                candidate.write_bytes(data)
                output = Path(self.temp.name) / 'rejected.tar.gz'
                with self.assertRaises(ValueError):
                    release.build_release(self.repo, output)
                self.assertFalse(output.exists())

    def test_only_pinned_prebuilt_is_allowed(self):
        known = (ROOT / release.PREBUILT).read_bytes()
        self.put(release.PREBUILT, known)
        self.put(release.PREBUILT + '.sha256', (release.PREBUILT_SHA256 + '\n').encode())
        self.assertEqual(release.source_contents(self.repo)[release.PREBUILT], known)
        self.put(release.PREBUILT, b'other executable')
        self.put(release.PREBUILT + '.sha256', hashlib.sha256(b'other executable').hexdigest().encode())
        with self.assertRaisesRegex(ValueError, 'Unrecognized prebuilt'):
            release.source_contents(self.repo)


if __name__ == '__main__':
    unittest.main()
