#!/usr/bin/env python3
"""Prepare BOTW shaders offline from verified local RomFS and rewritten modules."""
import argparse
import json
import os
from pathlib import Path
import struct

from prepare_shader_seed import bnsh_shaders, checked, digest, prepare, sarc, sharcb_shaders
from shader_list import ShaderList, canonical, VARIANT

CHAIN = 'botw-native-v1-control-size'
MIN_SHADERS = 8000
MAX_PACKAGE = 512 << 20
MAX_DEPTH = 8
NO_SHADER_LIST = Path(__file__).with_name('botw-no-shared-shader-list.tsv')
MODEL_TEXTURE = ('.bfres', '.sbfres', '.bntx', '.bflim', '.bftex', '.sbftex')
KNOWN = {}
AOC = None   # extracted/aoc when the build includes the DLC


def known_bindings(cache, output):
    """Import binding descriptions used by this title, never shader bytes from a played cache."""
    used, folder = set(), cache / ('rt-' + CHAIN + '.noindex')   # app/game_storage.h: nsw_shader_cache_path
    if not folder.is_dir():
        folder = cache / ('rt-' + CHAIN)   # not yet renamed by a launcher of this build
    for path in (cache / 'pso_user.tsv', folder / 'pso_collected.tsv'):
        if path.is_file():
            for line in path.read_text().splitlines():
                row = line.split('\t')
                if len(row) >= 3 and row[0] == 'S' and VARIANT.fullmatch(row[1]):
                    used.add(row[1][:-2])
    known, listing = {}, ShaderList()
    for key in sorted({variant[:64] for variant in used}):
        try:
            entries = json.loads((folder / (key + '.variants')).read_text())
        except (OSError, ValueError):
            continue
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or entry.get('variant') not in used:
                continue
            deps = canonical(entry.get('deps'))
            if deps is None:
                continue
            try:
                reflection = json.loads((folder / (entry['variant'] + '_b.json')).read_text())
            except (OSError, ValueError):
                continue
            if reflection.get('chain') != CHAIN or canonical(reflection.get('deps')) != deps:
                continue
            known.setdefault(key, []).append(json.loads(deps))
            listing.variants.add((key, deps))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp')
    listing.save(temporary)
    temporary.replace(output)
    return known


def coverage(key, stage, reflection, expand=True):
    """Common complete binding profiles plus locally observed mixed states; no Cartesian explosion."""
    deps = {name: reflection['deps'].get(name, {}) for name in ('tex', 'img', 'attr', 'fout')}
    profiles = [deps, *KNOWN.get(key, [])]
    for bag, types in (('tex', ('2darray', 'cube', '3d', '2d_depth', '2darray_depth')),
                       ('attr', ('uint', 'sint')), ('fout', ('uint', 'sint'))):
        if expand and deps[bag]:
            profiles.extend({**deps, bag: {slot: kind for slot in deps[bag]}} for kind in types)
    for profile in profiles:
        yield profile
        if stage == 'vs':
            yield {**profile, 'depth_minus_w': False}


def startup(key, stage, reflection):
    """App builds stay bounded: default and locally observed states; build_shaders.py adds the type profiles."""
    return coverage(key, stage, reflection, expand=False)


def yaz0(data):
    """Decode bounded Yaz0, including overlapping backreferences."""
    if checked(data, 0, 16)[:4] != b'Yaz0':
        raise ValueError('expected Yaz0 header')
    size = struct.unpack_from('>I', data, 4)[0]
    if size > MAX_PACKAGE:
        raise ValueError('unbounded Yaz0 shader package')
    out, position = bytearray(), 16
    while len(out) < size:
        flags = checked(data, position, 1)[0]
        position += 1
        for bit in range(7, -1, -1):
            if len(out) == size:
                break
            if flags & (1 << bit):
                out.extend(checked(data, position, 1))
                position += 1
                continue
            a, b = checked(data, position, 2)
            position += 2
            distance, length = ((a & 15) << 8 | b) + 1, a >> 4
            if length:
                length += 2
            else:
                length = checked(data, position, 1)[0] + 18
                position += 1
            if distance > len(out) or length > size - len(out):
                raise ValueError('Yaz0 backreference outside output')
            while length:
                count = min(length, distance)
                start = len(out) - distance
                out.extend(out[start:start + count])
                length -= count
    return bytes(out)


def candidate(name):
    return (name.startswith('Pack/') and name.endswith('.pack') or
            name.startswith('Effect/') and name.endswith('.sesetlist') or
            name.startswith('Layout/') and name.endswith('sblarc') or
            name.endswith(('.bnsh', '.bfsha', '.sbfsha', '.sharcb')))


def verified_index(romfs):
    index = json.loads((romfs / 'index.json').read_text())
    if index.get('status') != 'pass' or (romfs / 'romfs.bin').stat().st_size != index['romfs_size']:
        raise ValueError('RomFS has not passed integrity verification')
    return index


def inventory(romfs, modules=None):
    """The game RomFS, then the DLC image (AOC, paths prefixed aoc:) and the game modules."""
    index = verified_index(romfs)
    shaders, archives = {}, []

    def visit(name, data, keys, depth=0):
        if name.endswith(MODEL_TEXTURE) or '/GameData/' in '/' + name:
            return
        if depth > MAX_DEPTH or len(data) > MAX_PACKAGE:
            raise ValueError('shader package size or nesting limit exceeded')
        if data[:4] == b'Yaz0':
            visit(name, yaz0(data), keys, depth + 1)
            return
        if data[:4] == b'SARC':
            try:
                members = sarc(data)
            except (ValueError, struct.error) as error:
                raise ValueError(f'{name}: {error}') from error
            for member, raw in members.items():
                visit(name + '/' + member, raw, keys, depth + 1)
            return
        if data[:4] == b'BAHS':
            for key, stage, control, code in sharcb_shaders(data, short_control=True):
                shaders.setdefault(key, (stage, control, code, name))
                keys.add(key)
            return
        position = 0
        while (position := data.find(b'BNSH\0\0\0\0', position)) >= 0:
            size = struct.unpack('<I', checked(data, position + 28, 4))[0]
            for key, stage, control, code in bnsh_shaders(checked(data, position, size), short_control=True):
                shaders.setdefault(key, (stage, control, code, name))
                keys.add(key)
            position += max(size, 8)

    for folder, prefix in ((romfs, ''), *(((AOC, 'aoc:'),) if AOC else ())):
        table = verified_index(folder)
        with (folder / 'romfs.bin').open('rb') as stream:
            for row in sorted(table['files'], key=lambda row: row['path']):
                name = row['path']
                if not candidate(name):
                    continue
                offset, size = row['offset'], row['size']
                if not 0 <= offset <= table['romfs_size'] or not 0 <= size <= min(MAX_PACKAGE, table['romfs_size'] - offset):
                    raise ValueError('RomFS shader range outside image')
                stream.seek(offset)
                raw = stream.read(size)
                if len(raw) != size:
                    raise ValueError('short RomFS shader read')
                keys = set()
                visit(prefix + name, raw, keys)
                if keys:
                    archives.append(dict(path=prefix + name, sha256=digest(raw)))
    for module in sorted(Path(modules).glob('*.patched.bin')) if modules else []:
        if module.stat().st_size > MAX_PACKAGE:
            raise ValueError('unbounded shader module')
        raw, keys = module.read_bytes(), set()
        name = 'module:' + module.name[:-len('.patched.bin')]
        visit(name, raw, keys)
        if keys:
            archives.append(dict(path=name, sha256=digest(raw)))
    startup = set(shaders)
    if not startup:
        raise ValueError('no BOTW shaders discovered')
    print(f'discovered {len(shaders)} unique BOTW shaders; preparing default and extended binding profiles', flush=True)
    return index, shaders, startup, archives


def main():
    global KNOWN, AOC
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['romfs', 'output', 'translator', 'spirv-cross']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--modules', type=Path)
    parser.add_argument('--aoc', type=Path, help='extracted/aoc: also inventory the DLC image')
    parser.add_argument('--all-variants', action='store_true', help='every type profile (build_shaders.py); slow, unbounded by design')
    args = parser.parse_args()
    if NO_SHADER_LIST.exists():
        raise ValueError('BOTW preparation requires an empty shared shader list')
    cache = Path(os.environ.get('NSW_GAMES_DIR', Path.home() / 'Documents/NSW Games')) / 'Breath of the Wild/Shaders Cache'
    listing = args.output.with_name(args.output.name + '-known-bindings.tsv')
    KNOWN = known_bindings(cache, listing)
    AOC = args.aoc.resolve() if args.aoc else None
    prepare(args.romfs.resolve(), args.output.resolve(), args.translator.resolve(), args.spirv_cross.resolve(),
            args.version, shader_list=listing, modules=args.modules.resolve() if args.modules else None,
            inventory_fn=inventory, minimum_shaders=MIN_SHADERS, chain=CHAIN,
            variant_planner=coverage if args.all_variants else startup,
            extra_identity={'aoc_romfs_sha256': verified_index(AOC)['romfs_sha256']} if AOC else None)


if __name__ == '__main__':
    main()
