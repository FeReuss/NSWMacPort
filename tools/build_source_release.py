#!/usr/bin/env python3
"""Export a reproducible source archive from the current checkout; never publish it."""
import argparse
import gzip
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'MacNativeGames-source'
PREBUILT = 'games/totk/prebuilt/macos-arm64/totk_xlate'
PREBUILT_SHA256 = '6dd53d6486c96e3a95ae5fd49fdc35f75f6e603ff8ededa3d7250fd2e4fe1220'
EXCLUDED_PARTS = {
    '.git', '.github', '.venv', '__pycache__', 'tests', 'test', 'build', 'builds',
    'totk_build', 'botw_build', 'analysis', 'extracted', 'rewritten', 'tooling',
    'bin', 'obj', 'logs', 'log', 'cache', 'caches', 'shader-cache', 'shader-seed',
    'shader-libs', 'romfs', 'romfs-update', 'shared-fonts', 'fonts', 'firmware',
    'dlc', 'data', 'user-data', 'profile', 'profiles', 'save', 'saves', 'ghidra-projects',
}
PRIVATE_SUFFIXES = {
    '.keys', '.key', '.pem', '.p12', '.pfx', '.xci', '.xcz', '.nsp', '.nsz', '.nca',
    '.nso', '.nro', '.elf', '.bfttf', '.ttf', '.otf', '.sav', '.save', '.log', '.pyc',
    '.bin', '.dat', '.cache', '.metallib', '.spv', '.msl', '.icns', '.ipa', '.app',
    '.tik', '.cert', '.cnmt', '.nacp', '.bnsh', '.bntx', '.sarc', '.sharcb',
    '.gz', '.zip', '.tar', '.dylib', '.dll', '.a', '.o', '.so', '.dsym',
}
KEY_ASSIGNMENT = re.compile(
    r'^\s*(?:header_key|master_key_[0-9a-f]+|titlekek_[0-9a-f]+|'
    r'key_area_key_[a-z0-9_]+|[a-z0-9_]+_key_source|[0-9a-f]{32})\s*=\s*[0-9a-f]{32,}\s*$',
    re.IGNORECASE | re.MULTILINE,
)


def allowed_path(name):
    path = PurePosixPath(name)
    if (path.is_absolute() or '..' in path.parts or '\\' in name or
            any(ord(char) < 32 for char in name)):
        raise ValueError('Unsafe source path')
    parts = path.parts
    if not parts or any(part.lower() in EXCLUDED_PARTS for part in parts):
        return False
    if path.name == '.DS_Store' or path.suffix.lower() in PRIVATE_SUFFIXES:
        return False
    if re.fullmatch(r'[0-9a-f]{64}(?:-[0-9a-f]{12})?(?:_b)?\.(?:metal|json|code|variants)', path.name):
        return False
    if any(part.lower().endswith(('.app', '.dsym')) for part in parts):
        return False
    if len(parts) == 1:
        return (name in {'.gitignore', 'build_app.py', 'build_shaders.py', 'README.md', 'THIRD_PARTY_NOTICES.md', 'NOTICE'} or
                name.startswith('LICENSE') or bool(re.fullmatch(r'nsw[^/]*\.py', name)))
    return parts[0] in {'games', 'vendor', 'tools', 'docs'}


def source_contents(repo):
    """Read tracked and nonignored new files, excluding symlinks and private output."""
    result = subprocess.run(['git', '-C', str(repo), 'ls-files', '-co',
                             '--exclude-standard', '-z'], check=True, capture_output=True)
    files = {}
    for name in sorted(set(result.stdout.decode('utf-8').rstrip('\0').split('\0'))):
        if not name or not allowed_path(name):
            continue
        path = repo
        for part in PurePosixPath(name).parts:
            path /= part
            if path.is_symlink():
                break
        else:
            if not path.is_file():
                continue
            data = path.read_bytes()
            if name == PREBUILT:
                if hashlib.sha256(data).hexdigest() != PREBUILT_SHA256:
                    raise ValueError(f'Unrecognized prebuilt translator: {name}')
            else:
                if (data[:4] in {b'NSO0', b'NRO0', b'PFS0', b'HFS0', b'BNSH', b'BNTX',
                                  b'SARC', b'Yaz0', b'KIP1', b'NSZ\0', b'XCZ\0'} or
                        data[0x100:0x104] == b'HEAD' or data[0x200:0x204] in {b'NCA2', b'NCA3'}):
                    raise ValueError(f'Nintendo data detected: {name}')
                try:
                    text = data.decode('utf-8')
                except UnicodeDecodeError:
                    raise ValueError(f'Unknown binary file: {name}') from None
                if any(ord(char) < 32 and char not in '\t\n\r' for char in text):
                    raise ValueError(f'Unknown binary file: {name}')
                if KEY_ASSIGNMENT.search(text):
                    raise ValueError(f'Private key material detected: {name}')
            files[name] = data
    if PREBUILT in files:
        sidecar = files.get(PREBUILT + '.sha256', b'').decode('ascii').strip()
        if sidecar != PREBUILT_SHA256:
            raise ValueError('Prebuilt translator checksum sidecar differs')
    return files


def build_release(repo, output):
    files = source_contents(Path(repo).resolve())
    manifest = ''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n'
                       for name, data in sorted(files.items())).encode('utf-8')
    files['SOURCE_MANIFEST.sha256'] = manifest
    # No timestamp, owner, checkout path or original permissions enter the archive.
    with Path(output).open('xb') as raw:
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive:
                for name, data in sorted(files.items()):
                    entry = tarfile.TarInfo(f'{PREFIX}/{name}')
                    entry.size = len(data)
                    entry.mode = 0o755 if name == PREBUILT or name.endswith('.sh') else 0o644
                    archive.addfile(entry, io.BytesIO(data))
    return len(files) - 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='new .tar.gz file; existing files are not overwritten')
    args = parser.parse_args()
    try:
        count = build_release(ROOT, args.output)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Source export failed: {error}\n')
    print(f'Created {args.output}: {count} source files and SHA-256 manifest. Nothing published.')


if __name__ == '__main__':
    main()
