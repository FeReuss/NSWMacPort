#!/usr/bin/env python3
"""Rewrite every extracted NRO ahead of time, like the ExeFS modules.

    rewrite_nros.py --nro <work>/extracted/nro --output <work>/rewritten/nro [--jobs N]

patch_nso.py reads NSO files, so each NRO is first restated as an uncompressed NSO with the same segments, module ID and
symbol-table location (<work>/extracted/nro/nso/<name>); its bytes in memory are unchanged. Modules already rewritten
are skipped, so the step resumes.
"""
import argparse
import json
import os
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def nro_as_nso(nro):
    """NSO header + the NRO's text/ro/data as stored (the NRO header stays where it is, at the start of text)."""
    assert nro[0x10:0x14] == b'NRO0', 'not an NRO'
    (text_off, text_size, ro_off, ro_size, data_off, data_size, bss) = struct.unpack_from('<7I', nro, 0x20)
    assert text_off == 0 and ro_off == text_off + text_size and data_off == ro_off + ro_size, 'unexpected NRO layout'
    assert len(nro) >= data_off + data_size, 'truncated NRO'
    api, dynstr, dynsym = (struct.unpack_from('<II', nro, at) for at in (0x68, 0x70, 0x78))
    header = bytearray(0x100)
    header[0:4] = b'NSO0'
    for i, (off, size) in enumerate(((text_off, text_size), (ro_off, ro_size), (data_off, data_size))):
        struct.pack_into('<III', header, 0x10 + 0x10 * i, 0x100 + off, off, size)
    struct.pack_into('<I', header, 0x3C, bss)
    header[0x40:0x60] = nro[0x40:0x60]
    struct.pack_into('<III', header, 0x60, text_size, ro_size, data_size)
    struct.pack_into('<6I', header, 0x88, *api, *dynstr, *dynsym)
    return bytes(header) + nro[:data_off + data_size]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nro', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=os.cpu_count() or 8)
    args = parser.parse_args()
    manifest = json.loads((args.nro / 'manifest.json').read_text())
    nso_dir = args.nro / 'nso'
    nso_dir.mkdir(exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'DM_RULESET': 'native', 'DM_PRIVATE_GATES': '0'}

    def rewrite(module):
        name = module['name']
        if (args.output / f'{name}.patched.bin').is_file():
            return name, 'already rewritten'
        nso = nso_dir / name
        nso.write_bytes(nro_as_nso((args.nro / f'{name}.nro').read_bytes()))
        done = subprocess.run([sys.executable, HERE / 'third_party/d2r/patch_nso.py', nso, args.output, name],
                              env=env, capture_output=True, text=True)
        if done.returncode:
            raise SystemExit(f'{name}: rewrite failed\n{done.stdout[-2000:]}{done.stderr[-4000:]}')
        return name, done.stdout.strip().splitlines()[0] if done.stdout.strip() else 'ok'

    with ThreadPoolExecutor(args.jobs) as pool:
        for name, note in pool.map(rewrite, manifest['modules']):
            print(f'{name}: {note}', flush=True)


if __name__ == '__main__':
    main()
