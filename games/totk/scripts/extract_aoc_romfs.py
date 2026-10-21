#!/usr/bin/env python3
"""Extract BOTW's add-on content (DLC) from its NSPs with the pinned hactool, verifying every IVFC level.

BOTW 1.9.0 enables its DLC only when the add-on content list holds exactly two entries including index 1
(main+0x10cc2f8: ListAddOnContent(out, 0, 2) == 2), then mounts index 1 as "aoc". Index 1 (…F001) carries the data
of both packs; index 2 (…F002) holds only System/AocVersion.txt. Both are therefore required together.
Common-ticket title keys go to a private temporary file, never to arguments or logs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
from extract_exefs import load_keys, container_entries, common_tickets, nca_inventory, record_offset, require
from extract_romfs import index_romfs
from extract_update_romfs import copy_range, hactool_prod_keys, run_verified_hactool, write_title_keys
from prepare_romfs_backend import runtime_index

BASE_TITLE = 0x01007EF00011E000
DATA, LICENSE = f'{BASE_TITLE + 0x1001:016X}', f'{BASE_TITLE + 0x1002:016X}'   # add-on content indices 1 and 2


def identify(package, keys):
    """(title ID, data NCA record, its header, common tickets) of a single-content add-on NSP."""
    with package.open('rb') as f:
        kind, entries = container_entries(f, package.stat().st_size)
        require(kind == 'nsp', f'{package.name}: add-on content must be an NSP')
        tickets = common_tickets(f, entries)
        records = nca_inventory(f, entries, keys)
    titles = {r['title_id'] for r, _ in records}
    require(len(titles) == 1, f'{package.name}: expected one add-on content, found {sorted(titles)}')
    data = [(r, h) for r, h in records if r['content_type'] in (4, 5)]   # Data / PublicData
    require(len(data) == 1, f'{package.name}: expected one add-on content data NCA')
    record, header = data[0]
    rights = header[0x230:0x240]
    require(rights == bytes(16) or rights in tickets, f'{package.name}: missing common ticket')
    return titles.pop(), record, header, tickets


def extract_one(package, record, header, tickets, keys_path, hactool, work, failure_log):
    sections = [i for i in range(4) if struct.unpack_from('<II', header, 0x240 + i*16) != (0, 0) and header[0x400 + i*512 + 2] == 0]
    require(len(sections) == 1, f'{package.name}: expected one RomFS section')
    write_title_keys(work/'title.keys', tickets)
    copy_range(package, record_offset(record), record['size'], work/'aoc.nca')
    hactool_prod_keys(keys_path, work/'hactool.keys')
    image = work/'romfs.bin'
    command = [str(hactool.resolve()), '-x', '-y', '--disablekeywarns', '--suppresskeys', '-k', str(work/'hactool.keys'),
               f'--section{sections[0]}={image}', str(work/'aoc.nca')]
    run_verified_hactool(command, dict(os.environ, MNG_TITLE_KEYS=str(work/'title.keys')), work/'extract.log',
                         failure_log, f'{package.name}: add-on content verification failed')
    for name in ('aoc.nca', 'title.keys', 'hactool.keys'):
        (work/name).unlink()
    files = index_romfs(image)
    version = next((f for f in files if f['path'] == 'System/AocVersion.txt'), None)
    require(version is not None and 0 < version['size'] <= 16, f'{package.name}: no System/AocVersion.txt')
    with image.open('rb') as f:
        f.seek(version['offset'])
        text = f.read(version['size']).decode('ascii').strip()
        f.seek(0)
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    return dict(romfs_size=image.stat().st_size, romfs_sha256=digest, aoc_version=text, files=files)


def extract(packages, keys_path, hactool, output):
    require(not output.exists(), 'Output exists')
    keys = load_keys(keys_path)
    found = {}
    for package in packages:
        title, record, header, tickets = identify(package, keys)
        require(title not in found, f'{package.name}: add-on content {title} given twice')
        found[title] = (package, record, header, tickets)
    require(set(found) == {DATA, LICENSE},
            f'BOTW enables its DLC only with both packs: {DATA} (data) and {LICENSE} (Champions\' Ballad licence); '
            f'given {sorted(found) or "none"}')
    output.parent.mkdir(parents=True, exist_ok=True)
    require(shutil.disk_usage(output.parent).free > 2*found[DATA][1]['size'] + (1 << 30), 'Not enough free space for the DLC')
    with tempfile.TemporaryDirectory(prefix='aoc-', dir=output.parent) as tmp:
        result = Path(tmp)/'result'
        result.mkdir()
        contents = {}
        for title in (DATA, LICENSE):
            work = Path(tmp)/title
            work.mkdir()
            package, record, header, tickets = found[title]
            contents[title] = extract_one(package, record, header, tickets, keys_path, hactool, work,
                                          output.parent/(output.name + '-failure.log'))
        versions = {c['aoc_version'] for c in contents.values()}
        require(len(versions) == 1 and versions.pop().startswith('3.'), 'BOTW 1.9.0 needs add-on content version 3.x in both packs')
        (Path(tmp)/DATA/'romfs.bin').rename(result/'romfs.bin')   # the game mounts index 1 only
        (result/'runtime-index.bin').write_bytes(runtime_index(contents[DATA]['files']))
        report = dict(status='pass', indices=[1, 2], mounted=DATA, integrity='all six IVFC levels verified in both packs',
                      romfs_size=contents[DATA]['romfs_size'], romfs_sha256=contents[DATA]['romfs_sha256'],
                      contents={title: {k: v for k, v in c.items() if k != 'files'} for title, c in contents.items()},
                      files=contents[DATA]['files'])
        (result/'index.json').write_text(json.dumps(report, indent=2) + '\n')
        result.rename(output)
    print(json.dumps(dict(status='pass', files=len(report['files']),
                          contents={t: (c['aoc_version'], c['romfs_sha256']) for t, c in report['contents'].items()})))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('packages', type=Path, nargs='+')
    parser.add_argument('--keys', required=True, type=Path)
    parser.add_argument('--hactool', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    extract(args.packages, args.keys, args.hactool, args.output)
