#!/usr/bin/env python3
"""Verify and merge the SSBU base RomFS (base NSP, direct AES-CTR) with its 13.0.5 BKTR update (update NSP).

Game data and keys remain local. The update's IVFC tree authenticates every block of the merged image.
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / 'third_party/by3-scripts'))
from extract_exefs import load_keys, require
from extract_romfs import index_romfs

spec = importlib.util.spec_from_file_location('by3_merge_romfs', HERE / 'third_party/by3-scripts/merge_romfs.py')
by3_merge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(by3_merge)

TITLE = '01006A800016E000'


class DirectBase:   # the base Program NCA stores its whole RomFS (unlike Bayonetta 3's sparse one)
    def __init__(self, ctr, start, end, fs):
        require(fs[4] == 3, 'Expected a direct AES-CTR base RomFS')
        require(end <= ctr.size, 'Base RomFS exceeds stored NCA')
        self.ctr, self.start, self.size = ctr, start, end - start
        self.upper = fs[0x140:0x148][::-1]

    def read(self, offset, size):
        require(0 <= offset and offset + size <= self.size, 'Base RomFS read out of bounds')
        return self.ctr.read(self.start + offset, size, self.upper)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path, help='base game NSP (v0)')
    parser.add_argument('update', type=Path, help='13.0.5 update NSP')
    parser.add_argument('--keys', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    require(not args.output.exists(), 'Output already exists; choose a new directory')
    keys = load_keys(args.keys)
    bf, brec, bhdr, bctr = by3_merge.open_program(args.base, keys, TITLE)
    uf, urec, uhdr, uctr = by3_merge.open_program(args.update, keys, TITLE)
    with bf, uf:
        bstart, bend, bfs = by3_merge.romfs_section(bhdr)
        ustart, uend, ufs = by3_merge.romfs_section(uhdr)
        require(bfs[4] == 3 and ufs[4] == 4, 'Expected a direct base RomFS and a BKTR update RomFS')
        patched = by3_merge.Patched(uctr, ustart, uend, ufs, DirectBase(bctr, bstart, bend, bfs))
        args.output.mkdir(parents=True)
        image = args.output / 'romfs.bin'
        counts, size, sha = by3_merge.verify_ivfc(ufs, patched.total, patched.read, image)
        files = index_romfs(image)
        report = dict(status='pass', base=dict(source=str(args.base.resolve()), source_size=args.base.stat().st_size, program=brec),
                      update=dict(source=str(args.update.resolve()), source_size=args.update.stat().st_size, program=urec),
                      ivfc_blocks=counts, romfs_size=size, romfs_sha256=sha,
                      bytes_from_base=patched.from_base, bytes_from_update=patched.from_update, files=files,
                      integrity='FS headers and all six update IVFC levels of the merged image verified; RSA signatures not checked')
        (args.output / 'index.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(dict(status='pass', file_count=len(files), size=size, sha256=sha,
                              bytes_from_base=patched.from_base, bytes_from_update=patched.from_update)), flush=True)


if __name__ == '__main__':
    main()
