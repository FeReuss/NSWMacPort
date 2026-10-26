#!/usr/bin/env python3
"""Find host replacements of SDK functions whose return kind differs from the original's.

A host function written as "returns a Result (0 = success)" for an SDK function that returns a bool inverts its
meaning: nn::time::CalculateSpanBetween returned 0 for success and the Spirit Board refill never finished
(2026-10-24). This reads every {"<mangled name>", host_function} table entry under runtime/, the C return type of the
host function, and the original's code in the analysis ELFs: a function whose every return path ends with w0 = 0/1
(orr/mov #1, wzr, and #1, cset, ldrb) returns a bool. Prints the mismatches; tail calls are skipped (unknown kind).

usage: override_audit.py <runtime dir> <dir with sdk.elf main.elf subsdk0.elf>
Exit status 1 when a host function returns an integer type for a bool original (the case that inverts meaning).
"""
import glob
import os
import re
import subprocess
import sys


def host_entries(rt):
    entries = {}
    for f in glob.glob(os.path.join(rt, '*.c')) + glob.glob(os.path.join(rt, '*.m')):
        src = open(f, errors='replace').read()
        for m in re.finditer(r'\{\s*"(_Z[^"]+)"\s*,\s*(?:\([^)]*\)\s*)?&?([A-Za-z_]\w*)\s*\}', src):
            name, fn = m.groups()
            d = re.search(r'(?:^|\n)\s*(?:static\s+)?(?:inline\s+)?([A-Za-z_][\w \*]*?)\s*\b' + re.escape(fn) + r'\s*\(', src)
            entries[name] = (os.path.basename(f), fn, d.group(1).strip() if d else '?')
    return entries


def originals(elfdir):
    syms = {}
    for mod in ('sdk', 'main', 'subsdk0'):
        out = subprocess.run(['xcrun', 'llvm-nm', '-D', '-S', '--defined-only', os.path.join(elfdir, mod + '.elf')],
                             capture_output=True, text=True, check=True).stdout
        for line in out.splitlines():
            p = line.split()
            if len(p) == 4 and p[2] in 'TtWw':
                syms.setdefault(p[3], (mod, int(p[0], 16), int(p[1], 16)))
    return syms


BOOL_W0 = re.compile(r'(orr\s+w0, wzr, #0x1|mov\s+w0, #0x1|mov\s+w0, wzr|and\s+w0, w0, #0x1|cset\s+w0|ldrb\s+w0)')


def return_kind(elfdir, mod, addr, size):
    out = subprocess.run(['xcrun', 'llvm-objdump', '-d', '--no-show-raw-insn', f'--start-address={addr:#x}',
                          f'--stop-address={addr + min(size, 0x2000):#x}', os.path.join(elfdir, mod + '.elf')],
                         capture_output=True, text=True, check=True).stdout
    ins = [l.split('\t', 1)[1].strip() for l in out.splitlines() if re.match(r'\s+[0-9a-f]+:\s', l) and '\t' in l]
    w0 = []
    for i, x in enumerate(ins):
        if not x.startswith('ret'):
            continue
        for y in reversed(ins[max(0, i - 8):i]):
            if re.match(r'(mov|orr|and|cset|ldr|ldrb|csel|movk|sub|add)\s+[wx]0,', y):
                w0.append(y)
                break
            if y.startswith('bl'):
                w0.append('call')
                break
    if not w0:
        return 'tail' if not any(x.startswith('ret') for x in ins) else 'void'
    return 'bool' if all(BOOL_W0.match(y) for y in w0) else 'other'


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    rt, elfdir = sys.argv[1:]
    syms, bad = originals(elfdir), 0
    for name, (f, fn, rtype) in sorted(host_entries(rt).items(), key=lambda e: e[1]):
        if name not in syms:
            continue
        kind = return_kind(elfdir, *syms[name])
        integer = rtype not in ('bool', '_Bool', 'void') and re.search(r'int|unsigned|Result', rtype)
        if kind == 'bool' and integer:
            mod, addr, _ = syms[name]
            print(f'{f}:{fn} returns {rtype!r}; the original {mod}+{addr:#x} returns a bool: {name}')
            bad += 1
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
