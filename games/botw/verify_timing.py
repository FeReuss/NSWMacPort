#!/usr/bin/env python3
"""Verify BOTW's native VFR patch without running the game or using the GPU."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SHA = '6d48bff1b7f2692bd4f2efd3316b564877c50a10c6145610cf9a779b220538c6'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path, help='verified main.patched.bin from BOTW 1.9.0')
    args = parser.parse_args()
    with args.image.open('rb') as f:
        if hashlib.file_digest(f, 'sha256').hexdigest() != SHA:
            parser.error('main rewrite differs from the verified BOTW 1.9.0 image')
    with tempfile.TemporaryDirectory(prefix='botw-timing-') as folder:
        binary = Path(folder) / 'probe'
        subprocess.run(['xcrun', 'clang', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-I', ROOT / 'games/totk/runtime', ROOT / 'games/botw/timing_probe.c',
                        ROOT / 'games/botw/game_patches.c', '-o', binary], check=True)
        subprocess.run([binary, args.image.resolve(), 'baseline'], check=True)
        subprocess.run([binary, args.image.resolve()], check=True)
        for mode, reason in [('bad-word', 'original instructions differ'), ('bad-hash', 'verified 1.9.0')]:
            result = subprocess.run([binary, args.image.resolve(), mode], capture_output=True, text=True)
            if result.returncode != 21 or reason not in result.stderr:
                raise RuntimeError(f'{mode}: guard failed: {result.returncode}, {result.stderr}')
        print('Two negative controls rejected modified instructions and a different rewrite.')


if __name__ == '__main__':
    main()
