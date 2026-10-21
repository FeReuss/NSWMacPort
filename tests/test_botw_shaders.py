"""BOTW shader preparation validates Yaz0 and traverses only relevant local data."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'games/totk/scripts'))
import prepare_botw_shaders as botw


def yaz_literals(data):
    return b'Yaz0' + struct.pack('>I', len(data)) + bytes(8) + b''.join(
        b'\xff' + data[i:i + 8] for i in range(0, len(data), 8))


def archive(files):
    names, records, payload = bytearray(), [], bytearray()
    for name, data in files.items():
        records.append((0, 0x01000000 | len(names) // 4, len(payload), len(payload) + len(data)))
        names.extend(name.encode() + b'\0')
        names.extend(bytes(-len(names) % 4))
        payload.extend(data)
    start = 20 + 12 + len(records) * 16 + 8 + len(names)
    return (struct.pack('<4sHHIIHH', b'SARC', 20, 0xfeff, start + len(payload), start, 0x100, 0) +
            struct.pack('<4sHHI', b'SFAT', 12, len(records), 0x65) +
            b''.join(struct.pack('<4I', *record) for record in records) +
            struct.pack('<4sHH', b'SFNT', 8, 0) + names + payload)


def shader(fill):
    data = bytearray(0x1200)
    data[:4], data[12:14], data[96:100] = b'BNSH', b'\xff\xfe', b'grsc'
    struct.pack_into('<I', data, 28, len(data))
    struct.pack_into('<IQ', data, 124, 1, 0xc0)
    struct.pack_into('<Q', data, 0xc0 + 16, 0x100)
    struct.pack_into('<Q', data, 0x100 + 8, 0x200)
    struct.pack_into('<QQII', data, 0x208, 0x400, 0x1000, 0x100, 0x7d1)
    struct.pack_into('<9I', data, 0x400, 0x98761234, 1, 9, 0x120, 11, 0x7d0, 0, 0, 0x7d1)
    struct.pack_into('<II', data, 0xb00, 0x80, 0x100)
    struct.pack_into('<I', data, 0xb14, 0)
    data[0x1000:0x1100] = bytes([fill]) * 0x100
    return bytes(data)


def romfs(folder, files):
    rows, payload = [], bytearray()
    for name, data in files.items():
        rows.append(dict(path=name, offset=len(payload), size=len(data)))
        payload.extend(data)
    (folder / 'romfs.bin').write_bytes(payload)
    (folder / 'index.json').write_text(json.dumps(dict(status='pass', romfs_size=len(payload),
                                                     romfs_sha256=botw.digest(payload), files=rows)))


class BotwShaders(unittest.TestCase):
    def test_control_with_appended_reflection_hashes_only_base_control(self):
        data = bytearray(shader(2))
        extension, base = 0x426, 0x7d1
        struct.pack_into('<I', data, 0x21c, base + extension)
        struct.pack_into('<9I', data, 0x400, 0x98761234, 1, 9, 0x120, 11, 0x7d0, extension, 0, base + extension)
        rows = list(botw.bnsh_shaders(data, short_control=True))
        self.assertEqual(len(rows[0][2]), base)
        data[0x400 + base:0x400 + base + extension] = bytes([0x55]) * extension
        self.assertEqual(list(botw.bnsh_shaders(data, short_control=True)), rows)
        struct.pack_into('<I', data, 0x420, base + extension - 1)
        with self.assertRaises(ValueError):
            list(botw.bnsh_shaders(data, short_control=True))

    def test_short_control_is_opt_in_and_ignores_bytes_beyond_declared_size(self):
        data = bytearray(shader(1))
        struct.pack_into('<I', data, 0x21c, 0x7d1)
        struct.pack_into('<9I', data, 0x400, 0x98761234, 1, 9, 0x120, 11, 0x7d0, 0, 0, 0x7d1)
        self.assertEqual(list(botw.bnsh_shaders(data)), [])
        rows = list(botw.bnsh_shaders(data, short_control=True))
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0][2]), 0x7d1)
        data[0xbd1:0xc80] = bytes([0xff]) * (0xc80 - 0xbd1)
        self.assertEqual(list(botw.bnsh_shaders(data, short_control=True)), rows)
        data[0x400] ^= 1
        with self.assertRaisesRegex(ValueError, 'short NVN'):
            list(botw.bnsh_shaders(data, short_control=True))

    def test_yaz0_literals_overlap_extended_length_and_alignment_header(self):
        self.assertEqual(botw.yaz0(yaz_literals(b'0123456789')), b'0123456789')
        header = b'Yaz0' + struct.pack('>I', 9) + struct.pack('>II', 0x4000, 0)
        self.assertEqual(botw.yaz0(header + b'\xe0ABC\x40\x02'), b'ABCABCABC')
        header = b'Yaz0' + struct.pack('>I', 20) + bytes(8)
        self.assertEqual(botw.yaz0(header + b'\x80A\x00\x00\x01'), b'A' * 20)

    def test_yaz0_rejects_truncation_invalid_references_and_output_bounds(self):
        header = lambda size: b'Yaz0' + struct.pack('>I', size) + bytes(8)
        cases = [b'', b'Yaz0', b'other header?????', header(1), header(1) + b'\x80',
                 header(3) + b'\0\x10', header(3) + b'\0\x10\0',
                 header(20) + b'\x80A\0\0', header(4) + b'\xe0ABC\x40\x02',
                 header(botw.MAX_PACKAGE + 1)]
        for raw in cases:
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                botw.yaz0(raw)

    def test_nested_romfs_and_modules_are_all_startup_and_skip_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = shader(1), shader(2)
            package = archive({'System/shaders.ssarc': yaz_literals(archive({'Shader/a.sbfsha': b'FSHA' + first})),
                               'Model/ignored.sbfres': b'Yaz0',
                               'GameData/gamedata.ssarc': yaz_literals(archive({'/state.bgdata': b'state'}))})
            romfs(root, {'Pack/Bootup_Graphics.pack': package, 'Effect/a.sesetlist': yaz_literals(second),
                         'Layout/a.sblarc': yaz_literals(archive({'a.bnsh': first})), 'Model/ignored.bfres': b'Yaz0'})
            modules = root / 'modules'
            modules.mkdir()
            (modules / 'main.patched.bin').write_bytes(bytes(16) + first)
            _, shaders, startup, archives = botw.inventory(root, modules)
            self.assertEqual(len(shaders), 2)
            self.assertEqual(startup, set(shaders))
            self.assertTrue(all(key == botw.digest(control + code) for key, (_, control, code, _) in shaders.items()))
            self.assertEqual({row['path'] for row in archives},
                             {'Pack/Bootup_Graphics.pack', 'Effect/a.sesetlist', 'Layout/a.sblarc', 'module:main'})

    def test_dlc_image_is_inventoried_only_when_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, aoc = Path(tmp) / 'game', Path(tmp) / 'aoc'
            root.mkdir(); aoc.mkdir()
            romfs(root, {'Effect/a.sesetlist': yaz_literals(shader(1))})
            romfs(aoc, {'Pack/AocMainField.pack': archive({'Effect/b.bnsh': shader(2)}), 'Physics/x.hksc': b'none'})
            with patch.object(botw, 'AOC', None):
                self.assertEqual(len(botw.inventory(root)[1]), 1)
            with patch.object(botw, 'AOC', aoc):
                _, shaders, startup, archives = botw.inventory(root)
            self.assertEqual((len(shaders), startup), (2, set(shaders)))
            self.assertEqual({row['path'] for row in archives}, {'Effect/a.sesetlist', 'aoc:Pack/AocMainField.pack'})

    def test_agl_archive_controls_and_code_are_inventoried(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, original = bytearray(0x1100), shader(3)
            struct.pack_into('<8I', data, 0, 0x53484142, 9, len(data), 5, 0, 0x100, 0x100, 0x1000)
            struct.pack_into('<QIi', data, 0x100, 0, 0x7d1, 0x60)
            data[0x160:0x9e0] = original[0x400:0xc80]
            data[0x1000:0x1100] = original[0x1000:0x1100]
            romfs(root, {'Pack/a.pack': archive({'System/a.sharcb': bytes(data)})})
            _, shaders, startup, _ = botw.inventory(root)
            key = botw.digest(original[0x400:0xbd1] + original[0x1000:0x1100])
            self.assertEqual(set(shaders), {key})
            self.assertEqual(startup, {key})

    def test_inventory_rejects_unverified_out_of_range_and_deep_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = shader(1)
            for _ in range(botw.MAX_DEPTH + 1):
                raw = archive({'nested.ssarc': raw})
            romfs(root, {'Pack/a.pack': raw})
            with self.assertRaisesRegex(ValueError, 'nesting'):
                botw.inventory(root)
            index = json.loads((root / 'index.json').read_text())
            index['files'][0]['offset'] = index['romfs_size']
            (root / 'index.json').write_text(json.dumps(index))
            with self.assertRaisesRegex(ValueError, 'range'):
                botw.inventory(root)
            index['status'] = 'failed'
            (root / 'index.json').write_text(json.dumps(index))
            with self.assertRaisesRegex(ValueError, 'integrity'):
                botw.inventory(root)

    def test_profiles_expand_types_clip_and_observed_mixed_bindings(self):
        deps = dict(tex={'0': '2d', '3': '2d'}, img={}, attr={'1': 'float'}, fout={})
        mixed = {**deps, 'tex': {'0': 'cube', '3': '3d'}}
        with patch.dict(botw.KNOWN, {'a': [mixed]}, clear=True):
            profiles = list(botw.coverage('a', 'vs', {'deps': deps}))
        self.assertIn(mixed, profiles)
        self.assertIn({**mixed, 'depth_minus_w': False}, profiles)
        self.assertIn({**deps, 'tex': {'0': '2d_depth', '3': '2d_depth'}}, profiles)
        self.assertIn({**deps, 'attr': {'1': 'sint'}}, profiles)
        self.assertEqual(deps['tex'], {'0': '2d', '3': '2d'})
        self.assertEqual(len(profiles), 18)
        with patch.dict(botw.KNOWN, {'a': [mixed]}, clear=True):
            bounded = list(botw.startup('a', 'vs', {'deps': deps}))
        self.assertEqual(bounded, [deps, {**deps, 'depth_minus_w': False}, mixed, {**mixed, 'depth_minus_w': False}])

    def test_only_used_matching_binding_descriptions_are_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / 'cache'
            folder = cache / ('rt-' + botw.CHAIN)
            folder.mkdir(parents=True)
            key, variant = 'a' * 64, 'a' * 64 + '-' + 'b' * 12
            deps = dict(tex={'2': 'cube'}, img={}, attr={}, fout={})
            (cache / 'pso_user.tsv').write_text(f'S\t{variant}_b\t1\n')
            (folder / (key + '.variants')).write_text(json.dumps([
                dict(variant=variant, deps=deps), dict(variant='../escape', deps=deps)]))
            reflection = folder / (variant + '_b.json')
            reflection.write_text(json.dumps(dict(chain=botw.CHAIN, deps=deps)))
            target = root / 'known.tsv'
            self.assertEqual(botw.known_bindings(cache, target), {key: [deps]})
            self.assertEqual(len(botw.ShaderList.load(target).variants), 1)
            reflection.write_text(json.dumps(dict(chain='wrong-title', deps=deps)))
            self.assertEqual(botw.known_bindings(cache, target), {})
            self.assertEqual(len(botw.ShaderList.load(target).variants), 0)

    def test_cli_uses_botw_inventory_chain_and_extended_profiles(self):
        args = ['prepare_botw_shaders.py', '--romfs', 'romfs', '--output', 'output', '--translator', 'translator',
                '--spirv-cross', 'spirv-cross', '--version', 'v0', '--modules', 'modules']
        with patch.object(sys, 'argv', args), patch.object(botw, 'prepare') as prepare, \
                patch.object(botw, 'known_bindings', return_value={}):
            botw.main()
        kwargs = prepare.call_args.kwargs
        self.assertIs(kwargs['inventory_fn'], botw.inventory)
        self.assertEqual(kwargs['minimum_shaders'], botw.MIN_SHADERS)
        self.assertEqual(kwargs['chain'], botw.CHAIN)
        self.assertEqual(kwargs['shader_list'].name, 'output-known-bindings.tsv')
        self.assertIs(kwargs['variant_planner'], botw.startup)
        with patch.object(sys, 'argv', [*args, '--all-variants']), patch.object(botw, 'prepare') as prepare, \
                patch.object(botw, 'known_bindings', return_value={}):
            botw.main()
        self.assertIs(prepare.call_args.kwargs['variant_planner'], botw.coverage)


if __name__ == '__main__':
    unittest.main()
