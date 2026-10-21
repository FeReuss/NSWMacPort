#!/usr/bin/env python3
"""NSWMacPort quick start: install the build dependencies, find your game files and keys, and build the app.

Runs with the macOS system python3 (3.9 or later); the build itself then uses Homebrew's python3.14.
Nothing is downloaded from Nintendo and nothing leaves your Mac: you supply your own dumps.
"""
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BREW = Path('/opt/homebrew/bin/brew')
PACKAGES = ('opus', 'libvpx', 'python@3.14')
GAMES = {
    'botw': {'name': 'Breath of the Wild', 'base': '01007EF00011E000', 'update': '01007EF00011E800',
             'dlc': ('01007EF00011F001', '01007EF00011F002'), 'update_required': True},
    'totk': {'name': 'Tears of the Kingdom', 'base': '0100F2C0115B6000', 'update': '0100F2C0115B6800',
             'dlc': (), 'update_required': False},
}
PACKAGE_SUFFIXES = ('.nsp', '.xci', '.nsz')


def clean_path(text):
    """A path typed or dragged onto Terminal: quotes and backslash escapes removed, ~ expanded."""
    text = text.strip()
    if not text:
        return None
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = [text.strip('\'"')]
    return Path(' '.join(parts)).expanduser() if parts else None


def discover(folder, game, depth=4):
    """Files of one game under a folder, classified by the title ID in their names (how dump tools name them)."""
    info, found = GAMES[game], {'base': None, 'update': None, 'dlc': [], 'keys': None, 'firmware': None}
    folder = Path(folder)
    for current, dirs, files in os.walk(folder):
        if len(Path(current).relative_to(folder).parts) >= depth:
            dirs[:] = []
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not d.endswith(('.app', '_build', '.noindex')))
        if found['firmware'] is None and sum(f.lower().endswith('.nca') for f in files) >= 50:
            found['firmware'] = Path(current)
        for name in sorted(files):
            path, upper = Path(current) / name, name.upper()
            if name == 'prod.keys' and found['keys'] is None:
                found['keys'] = path
            if not name.lower().endswith(PACKAGE_SUFFIXES):
                continue
            if info['base'] in upper and found['base'] is None:
                found['base'] = path
            elif info['update'] in upper and found['update'] is None:
                found['update'] = path
            elif any(t in upper for t in info['dlc']) and path not in found['dlc']:
                found['dlc'].append(path)
    found['dlc'].sort()
    return found


def build_command(python, game, base, keys, update=None, dlc=(), firmware=None, out=None, shaders_only=False):
    """The build_app.py (or build_shaders.py) command for these inputs."""
    script = ROOT / ('build_shaders.py' if shaders_only else 'build_app.py')
    cmd = [str(python), str(script), '--game', game, str(base), '-k', str(keys)]
    if update:
        cmd += ['--update', str(update)]
    for pack in dlc:
        cmd += ['--dlc', str(pack)]
    if firmware and not shaders_only:
        cmd += ['--firmware', str(firmware)]
    if out and not shaders_only:
        cmd += ['-o', str(out)]
    return cmd


def ask(question, default=None):
    suffix = f' [{default}]' if default else ''
    answer = input(f'{question}{suffix}: ').strip()
    return answer or (default or '')


def yes(question, default=False):
    answer = ask(question + (' (Y/n)' if default else ' (y/N)')).lower()
    return default if not answer else answer.startswith('y')


def ask_path(label, suggestion=None, required=True, kind='file'):
    while True:
        answer = clean_path(ask(f'{label} (drag it here)', str(suggestion) if suggestion else None))
        if answer is None:
            if not required:
                return None
            print('  This one is required.')
            continue
        if (kind == 'file' and answer.is_file()) or (kind == 'dir' and answer.is_dir()):
            return answer
        print(f'  Not found: {answer}')


def check_system():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        sys.exit('NSWMacPort needs a Mac with Apple silicon.')
    major = int(platform.mac_ver()[0].split('.')[0] or 0)
    if major < 26:
        sys.exit(f'NSWMacPort needs macOS 26 or later (Metal 4); this Mac runs {platform.mac_ver()[0]}.')
    if subprocess.run(['xcode-select', '-p'], capture_output=True).returncode:
        subprocess.run(['xcode-select', '--install'])
        sys.exit('Install the Xcode command line tools in the window that opened, then run this script again.')


def install_dependencies():
    if not BREW.exists():
        sys.exit('Homebrew is needed. Install it from https://brew.sh (one command in Terminal), then run this script again.')
    have = subprocess.run([str(BREW), 'list', '--formula', '-1'], capture_output=True, text=True).stdout.split()
    missing = [p for p in PACKAGES if p not in have]
    if missing:
        print(f'Installing with Homebrew: {" ".join(missing)}')
        subprocess.run([str(BREW), 'install', *missing], check=True)
    python = shutil.which('python3.14', path='/opt/homebrew/bin')
    if not python:
        sys.exit('python3.14 was not found in /opt/homebrew/bin after installing python@3.14.')
    return python


def main():
    print('NSWMacPort quick start. Use only your own game, update, DLC, keys and firmware dumps.\n')
    check_system()
    python = install_dependencies()
    choice = ask('Game: 1 = Breath of the Wild, 2 = Tears of the Kingdom', '1')
    game = 'totk' if choice.strip() in ('2', 'totk') else 'botw'
    info = GAMES[game]
    found = {'base': None, 'update': None, 'dlc': [], 'keys': None, 'firmware': None}
    folder = clean_path(ask('A folder that holds your files, to search it (optional, Enter to skip)', ''))
    if folder and folder.is_dir():
        found = discover(folder, game)
        for key in ('base', 'update', 'keys', 'firmware'):
            print(f'  {key:8} {found[key] or "-"}')
        for pack in found['dlc']:
            print(f'  dlc      {pack}')
    base = ask_path(f'{info["name"]} base game (NSP or XCI, title {info["base"]})', found['base'])
    update = ask_path(f'Update NSP/NSZ (title {info["update"]}{", required: 1.9.0" if game == "botw" else ", optional"})',
                      found['update'], required=info['update_required'])
    dlc = []
    if game == 'botw':
        if len(found['dlc']) == 2 and yes('Include both DLC packs found above?', True):
            dlc = found['dlc']
        elif yes('Include the DLC (needs both packs)?'):
            dlc = [ask_path('DLC pack 1 NSP (01007EF00011F001)'), ask_path('DLC pack 2 NSP (01007EF00011F002)')]
    keys = ask_path('prod.keys from your Switch', found['keys'])
    firmware = None
    if base.suffix.lower() == '.nsp' or found['firmware']:
        firmware = ask_path('Firmware folder with .nca files (needed for an NSP: system fonts)', found['firmware'],
                            required=base.suffix.lower() == '.nsp', kind='dir')
    out = clean_path(ask('Output app (Enter: next to the game file)', ''))
    cmd = build_command(python, game, base, keys, update, dlc, firmware, out)
    print('\nBuilding with:\n  ' + ' '.join(shlex.quote(c) for c in cmd) + '\n')
    if subprocess.run(cmd, cwd=ROOT).returncode:
        sys.exit('The build stopped; its message above names the step and the log to read.')
    if yes('Prepare the complete shader cache now (about 35 minutes the first time, fewer stutters later)?', game == 'botw'):
        shaders = build_command(python, game, base, keys, update, dlc, shaders_only=True)
        subprocess.run(shaders, cwd=ROOT, check=True)
    print('\nDone. Open the app from Finder. Player data: ~/Documents/NSW Games/' + info['name'])


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit('\nStopped.')
