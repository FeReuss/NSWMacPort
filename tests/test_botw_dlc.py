"""BOTW DLC: both packs or none, verified extraction, and the build's rejection of a single pack. Synthetic bytes only."""
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from test_game_containers import HEADER, AREA, TITLEKEK, nca, partition, ticket
from extract_aoc_romfs import DATA, LICENSE, extract
import build_app
from games.botw.build import build_botw


def version_romfs(text):
    """A RomFS holding System/AocVersion.txt, as both BOTW packs do."""
    dirs = (struct.pack('<6I', 0, 0xffffffff, 24, 0xffffffff, 0xffffffff, 0) +
            struct.pack('<6I', 0, 0xffffffff, 0xffffffff, 0, 0xffffffff, 6) + b'System\0\0')
    files = struct.pack('<IIQQII', 24, 0xffffffff, 0, len(text), 0xffffffff, 14) + b'AocVersion.txt\0\0'
    data = 80 + len(dirs) + len(files)
    return struct.pack('<10Q', 80, 80, 0, 80, len(dirs), 80 + len(dirs), 0, 80 + len(dirs), len(files), data) + dirs + files + text


def package(title):
    return partition([('data.nca', nca(True, title=int(title, 16), content=5)),
                      ('meta.cnmt.nca', nca(title=int(title, 16), content=1)), ('common.tik', ticket())])


class BotwDlc(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.keys = self.root / 'prod.keys'
        self.keys.write_text('\n'.join(f'{n} = {k.hex()}' for n, k in
                             [('header_key', HEADER), ('key_area_key_application_00', AREA), ('titlekek_00', TITLEKEK)]))
        self.packs = {}
        for title in (DATA, LICENSE):
            self.packs[title] = self.root / f'{title}.nsp'
            self.packs[title].write_bytes(package(title))
        self.hactool = self.root / 'hactool'
        self.hactool.write_text('#!' + sys.executable + '\n'
            'import os,sys,pathlib,stat\n'
            'for key in (pathlib.Path(sys.argv[sys.argv.index("-k")+1]), pathlib.Path(os.environ["MNG_TITLE_KEYS"])):\n'
            '    assert stat.S_IMODE(key.stat().st_mode) == 0o600\n'
            'if os.environ.get("FAKE_HACTOOL_FAIL"): print("Key ("+"ab"*17+") must be 32 hex digits!"); raise SystemExit(1)\n'
            'out=pathlib.Path(next(s.split("=",1)[1] for s in sys.argv if s.startswith("--section0=")))\n'
            f'out.write_bytes(eval(os.environ.get("FAKE_AOC", {version_romfs(b"3.0")!r}.__repr__())))\n'
            'for i in range(6): print(f"Level {i} (GOOD)")\n')
        self.hactool.chmod(0o755)

    def test_both_packs_in_any_order_give_the_mounted_data_image_and_list(self):
        out = self.root / 'aoc'
        extract([self.packs[LICENSE], self.packs[DATA]], self.keys, self.hactool, out)
        report = json.loads((out / 'index.json').read_text())
        self.assertEqual((report['indices'], report['mounted']), ([1, 2], DATA))
        self.assertEqual({c['aoc_version'] for c in report['contents'].values()}, {'3.0'})
        self.assertEqual(report['romfs_size'], (out / 'romfs.bin').stat().st_size)
        index = (out / 'runtime-index.bin').read_bytes()
        self.assertEqual(index[:8], b'TOTKRFS1')
        self.assertEqual(struct.unpack_from('<Q', index, 8)[0], len(report['files']))
        self.assertEqual(sorted(p.name for p in out.iterdir()), ['index.json', 'romfs.bin', 'runtime-index.bin'])
        self.assertFalse(list(self.root.glob('aoc-*')))   # no NCA or key file left behind

    def test_a_single_pack_or_a_repeated_pack_is_rejected(self):
        for given, message in (([self.packs[DATA]], 'only with both packs'), ([self.packs[LICENSE]], 'only with both packs'),
                               ([self.packs[DATA], self.packs[DATA]], 'given twice')):
            with self.assertRaisesRegex(ValueError, message):
                extract(given, self.keys, self.hactool, self.root / 'aoc')

    def test_other_versions_and_failed_verification_are_rejected_without_key_material(self):
        with patch.dict(os.environ, FAKE_AOC=repr(version_romfs(b'2.0'))), self.assertRaisesRegex(ValueError, 'version 3'):
            extract(list(self.packs.values()), self.keys, self.hactool, self.root / 'old')
        with patch.dict(os.environ, FAKE_HACTOOL_FAIL='1'), self.assertRaisesRegex(ValueError, 'verification failed'):
            extract(list(self.packs.values()), self.keys, self.hactool, self.root / 'failed')
        failure = (self.root / 'failed-failure.log').read_text()
        self.assertNotIn('ab' * 17, failure)
        self.assertFalse((self.root / 'failed').exists())

    def test_build_rejects_one_pack_before_any_work_and_passes_both_on(self):
        for name in ('base.nsp', 'update.nsp'):
            (self.root / name).write_bytes(b'PFS0')
        common = ['--game', 'botw', str(self.root / 'base.nsp'), '-k', str(self.keys), '--update', str(self.root / 'update.nsp'),
                  '--work', str(self.root / 'work'), '--shaders-only']
        methods = {name: Mock() for name in ('step_code', 'step_update_data', 'step_shaders', 'step_shader_libs',
                                            'install_shader_cache', 'prerequisites')}
        methods['prepare_work'] = Mock(return_value=self.root / 'tree')
        with patch.multiple(build_app, **methods):
            with self.assertRaisesRegex(build_app.BuildError, 'only with both packs'):
                build_botw(build_app.parse_args([*common, '--dlc', str(self.packs[DATA])]), build_app)
            methods['prepare_work'].assert_not_called()
            with patch('games.botw.build.extract_dlc', return_value=self.root / 'aoc') as dlc, \
                    patch('games.botw.build.shutil.disk_usage', return_value=Mock(free=64 * 2**30)):
                build_botw(build_app.parse_args([*common, '--dlc', str(self.packs[DATA]), '--dlc', str(self.packs[LICENSE])]), build_app)
        self.assertEqual([p.name for p in dlc.call_args.args[1]], [f'{DATA}.nsp', f'{LICENSE}.nsp'])
        self.assertEqual(methods['step_shaders'].call_args.kwargs['aoc'], self.root / 'aoc')


if __name__ == '__main__':
    unittest.main()
