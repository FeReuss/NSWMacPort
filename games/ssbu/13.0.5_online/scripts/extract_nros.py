#!/usr/bin/env python3
"""Extract the NRO modules (the fighters' lua2cpp code) that SSBU 13.0.5 loads with nn::ro from its data.arc.

    extract_nros.py --romfs <work>/extracted/local/romfs --main <work>/extracted/main --output <work>/extracted/nro

Every file whose extension hash is "nro" is read, zstd-decompressed and accepted only when its SHA-256 is listed in one
of the game's own .nrr registration files (the list nn::ro checks on the console), so a wrong table walk cannot pass.
Names come from main's DT_NEEDED entries (lua2cpp_<name>.nrs -> prebuilt:/nro/release/lua2cpp_<name>.nro); a module
with no matching name keeps its path hash. Writes <output>/<name>.nro and <output>/manifest.json. Keys and game data
stay local; nothing here is game data.

The data.arc layout (header, compressed file-system table, file lookup) was written from the field descriptions in
jam1garner/smash-arc (MIT), used as format documentation only; see THIRD_PARTY_NOTICES.md.
"""
import argparse
import bisect
import hashlib
import json
import struct
import zlib
from compression import zstd
from pathlib import Path

import lz4.block

ARC_MAGIC = 0xABCDEF9876543210


def require(condition, message):
    if not condition:
        raise SystemExit(f'extract_nros: {message}')


def hash40(text):
    data = text.encode()
    return (len(data) << 32) | zlib.crc32(data)


def h2i(value):   # HashToIndex: crc32 (bits 0-31), length (32-39), index (40-63)
    return value & 0xFF_FFFF_FFFF, value >> 40


class Reader:
    def __init__(self, data):
        self.data, self.at = data, 0

    def take(self, fmt, count=1):
        size = struct.calcsize('<' + fmt)
        out = [struct.unpack_from('<' + fmt, self.data, self.at + i * size) for i in range(count)]
        self.at += size * count
        require(self.at <= len(self.data), 'file-system table is shorter than its counts say')
        return out


def file_system(arc, fs_offset):
    """Decompress and walk the file-system table; returns the lists the lookup needs."""
    arc.seek(fs_offset)
    magic, decomp, comp, _section = struct.unpack('<IIII', arc.read(16))
    require(magic == 0x10, 'unexpected compressed-table header')
    table = zstd.decompress(arc.read(comp))
    require(len(table) == decomp, 'file-system table size')
    r = Reader(table)
    (table_size, path_count, index_count, folder_count, folder_offsets_1, hash_folder_count, info_count, info_sub_count,
     data_count, folder_offsets_2, data_count_2, _pad, _a, _b, _r1, _r2, _p2, _version, extra_folder, extra_count, _u1, _u2,
     extra_count_2, extra_sub_count) = r.take('14IBBH7I')[0]
    require(table_size == decomp, 'file-system header size')
    r.at = 0x100
    quick, stream_hashes, stream_indices, stream_datas = r.take('4I')[0]
    r.take('3I', quick); r.take('Q', stream_hashes); r.take('QI', stream_hashes); r.take('I', stream_indices)
    r.take('QQ', stream_datas)
    groups, buckets = r.take('II')[0]
    fs = dict(buckets=r.take('II', buckets), hash_to_path=[h2i(v) for (v,) in r.take('Q', groups)])
    fs['paths'] = r.take('4Q', path_count)
    fs['info_indices'] = r.take('II', index_count)
    r.take('Q', folder_count); r.take('QQQ6II', folder_count)   # directory lookup and infos: not needed here
    fs['folder_offsets'] = r.take('Q5I', folder_offsets_1 + folder_offsets_2 + extra_folder)
    r.take('Q', hash_folder_count)
    fs['infos'] = r.take('4I', info_count + data_count_2 + extra_count)
    fs['info_to_data'] = r.take('3I', info_sub_count + data_count_2 + extra_count_2)
    fs['datas'] = r.take('4I', data_count + data_count_2 + extra_sub_count)
    fs['unread'] = len(table) - r.at   # 13.0.5 keeps ~300 KB after the last list; the .nrr check covers the walk
    return fs


def locate(fs, path_index):
    """(offset in the file section, compressed size, size, flags) of one file, the way the game resolves it."""
    info_index = fs['info_indices'][h2i(fs['paths'][path_index][0])[1]][1]
    _path, _indice, to_data, flags = fs['infos'][info_index]
    require(not flags & (1 << 15 | 1 << 16), 'regional or localized NRO')   # NROs exist once; regional files never seen
    folder, data_index, _ = fs['info_to_data'][to_data]
    offset_in_folder, comp, size, data_flags = fs['datas'][data_index]
    return fs['folder_offsets'][folder][0] + (offset_in_folder << 2), comp, size, data_flags


def nrr_hashes(romfs):
    """SHA-256 values registered by the game's .nrr files (header 0x340: hash offset, count)."""
    index = json.loads((romfs / 'index.json').read_text())
    found = set()
    with (romfs / 'romfs.bin').open('rb') as f:
        for item in index['files']:
            if not item['path'].startswith('.nrr/'):
                continue
            f.seek(item['offset']); data = f.read(item['size'])
            require(data[:4] == b'NRR0', f'{item["path"]} is not an NRR')
            offset, count = struct.unpack_from('<II', data, 0x340)
            require(offset + 32 * count <= len(data), f'{item["path"]} hash list out of bounds')
            found.update(data[offset + 32 * i:offset + 32 * i + 32].hex() for i in range(count))
    return index, found


def needed_names(main_nso):
    """lua2cpp module names from main's DT_NEEDED (the .nrs stubs it links against)."""
    d = main_nso.read_bytes()
    require(d[:4] == b'NSO0', 'main is not an NSO')
    flags = struct.unpack_from('<I', d, 0xC)[0]
    segs = [struct.unpack_from('<III', d, 0x10 + 0x10 * i) for i in range(3)]
    csz = struct.unpack_from('<III', d, 0x60)
    image = bytearray(segs[2][1] + segs[2][2])
    for i, (file_off, mem_off, size) in enumerate(segs):
        raw = d[file_off:file_off + csz[i]]
        image[mem_off:mem_off + size] = lz4.block.decompress(raw, uncompressed_size=size) if flags >> i & 1 else raw
    mod0 = struct.unpack_from('<I', image, 4)[0]
    dyn = mod0 + struct.unpack_from('<i', image, mod0 + 4)[0]
    tags, needed = {}, []
    for at in range(dyn, len(image), 16):
        tag, value = struct.unpack_from('<qQ', image, at)
        if tag == 0: break
        if tag == 1: needed.append(value)
        tags.setdefault(tag, value)
    names = []
    for value in needed:
        start = tags[5] + value
        name = image[start:image.index(0, start)].decode().rsplit('/', 1)[-1]
        if name.endswith('.nrs'):
            names.append(name[:-4])
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--romfs', type=Path, required=True, help='merged RomFS folder (romfs.bin + index.json)')
    parser.add_argument('--main', type=Path, required=True, help='the 13.0.5 main NSO (for module names)')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), 'output already exists')
    index, registered = nrr_hashes(args.romfs)
    arc_item = next(i for i in index['files'] if i['path'] == 'data.arc')
    names = {hash40(f'prebuilt:/nro/release/{n}.nro'): n for n in needed_names(args.main)}
    nro_ext = hash40('nro')
    modules = []
    with (args.romfs / 'romfs.bin').open('rb') as romfs:
        base = arc_item['offset']
        romfs.seek(base)
        magic, _stream, file_section, _shared, fs_offset, _search = struct.unpack('<6Q', romfs.read(48))
        require(magic == ARC_MAGIC, 'data.arc magic')
        fs = file_system(romfs, base + fs_offset)
        buckets = fs['buckets']
        for path_index, (path, ext, _parent, _name) in enumerate(fs['paths']):
            if h2i(ext)[0] != nro_ext:
                continue
            path_hash = h2i(path)[0]
            # The lookup the game does: bucket by hash, then the path index in that bucket (checks the walk end to end).
            start, count = buckets[path_hash % len(buckets)]
            bucket = fs['hash_to_path'][start:start + count]
            at = bisect.bisect_left([h for h, _ in bucket], path_hash)
            require(at < len(bucket) and bucket[at] == (path_hash, path_index), f'path {path_hash:#x} not in its bucket')
            offset, comp, size, flags = locate(fs, path_index)
            romfs.seek(base + file_section + offset)
            data = romfs.read(comp)
            if flags & 1:
                require(flags & 2, 'NRO compressed with something other than zstd')
                data = zstd.decompress(data)
            require(len(data) == size and data[0x10:0x14] == b'NRO0', f'path {path_hash:#x} is not an NRO')
            digest = hashlib.sha256(data).hexdigest()
            require(digest in registered, f'path {path_hash:#x}: SHA-256 not registered in any .nrr')
            name = names.get(path_hash, f'nro_{path_hash:010x}')
            modules.append(dict(name=name, path_hash40=f'{path_hash:#012x}', size=size, sha256=digest,
                                build_id=data[0x40:0x60].hex()))
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / f'{name}.nro').write_bytes(data)
    require(modules, 'no NRO found in data.arc')
    require(len({m['name'] for m in modules}) == len(modules), 'duplicate NRO names')
    modules.sort(key=lambda m: m['name'])
    unregistered = len(registered - {m['sha256'] for m in modules})
    manifest = dict(status='pass', modules=modules, table_bytes_after_lists=fs['unread'], nrr_hashes=len(registered), nrr_hashes_without_module=unregistered,
                    integrity='each NRO SHA-256 is listed in the game\'s own .nrr files')
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(dict(status='pass', modules=len(modules), named=sum(not m['name'].startswith('nro_') for m in modules),
                          bytes=sum(m['size'] for m in modules), nrr_hashes=len(registered),
                          nrr_hashes_without_module=unregistered)))


if __name__ == '__main__':
    main()
