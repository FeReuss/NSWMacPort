#!/usr/bin/env python3
"""Prepare BOTW code for port development. Does not build an app or run the game."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'games/totk/scripts'
REWRITER = ROOT / 'games/totk/third_party/d2r'
sys.path[:0] = [str(ROOT), str(SCRIPTS)]
from build_app import npdm_signed
from extract_exefs import extract_exefs, require
from prepare_modules import prepare

TITLE = '01007EF00011E000'


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def prepare_code(base, update, keys, output):
    require(not output.exists(), 'Output already exists; choose a new directory')
    for path in (base, update, keys):
        require(path.is_file(), f'No such input: {path}')
    report = dict(status='code_prepared', playable=False, title_id=TITLE, modules={}, rewrites={})
    for label, source in (('base', base), ('update', update)):
        print(f'Verifying {label} ExeFS and NSO segments...', flush=True)
        exefs = output / label / 'exefs'
        manifest = extract_exefs(source, keys, exefs, title_id=TITLE, update=label == 'update')
        require(npdm_signed(exefs / 'main.npdm'), f'{label}: invalid Nintendo NPDM signature')
        elf = output / label / 'elf'
        elf.mkdir()
        modules = []
        for row in manifest['files']:
            path = exefs / row['name']
            with path.open('rb') as f:
                if f.read(4) != b'NSO0':
                    continue
            inv = prepare(path, elf / (path.name + '.elf'))
            (elf / (path.name + '-inventory.json')).write_text(json.dumps(inv, indent=2) + '\n')
            modules.append({key: inv[key] for key in ('module', 'build_id', 'nso_sha256', 'needed')})
        report['modules'][label] = modules
    rewritten = output / 'rewritten/update'
    rewritten.mkdir(parents=True)
    for row in report['modules']['update']:
        name = row['module']
        if name == 'rtld':
            continue  # The native loader replaces rtld; no guest loader is executed.
        print(f'Rewriting update module {name}...', flush=True)
        log = rewritten / (name + '.log')
        with log.open('w') as f:
            result = subprocess.run([sys.executable, REWRITER / 'patch_nso.py', output / 'update/exefs' / name,
                                     rewritten, name], env=dict(os.environ, DM_RULESET='native', DM_PRIVATE_GATES='0'),
                                    stdout=f, stderr=subprocess.STDOUT)
        require(result.returncode == 0, f'{name}: rewrite failed; see {log}')
        report['rewrites'][name] = digest(rewritten / (name + '.patched.bin'))
    report['rewriter_sources'] = {name: digest(REWRITER / name) for name in
                                  ('patch_nso.py', 'jump_table.py', 'call_window.py', 'relr.py')}
    report['limitations'] = [
        'No BOTW native runtime, app bundle, boot or gameplay validation.',
        'Rewriting does not prove semantic equivalence; run structck.py and equiv.py separately.',
        'RomFS update merging, DLC mounting, shaders, audio, input and saves are not adapted.',
        'NPDM signature and internal hashes do not authenticate every game-content byte.',
    ]
    (output / 'code-report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    parser.add_argument('--update', type=Path, required=True)
    parser.add_argument('--keys', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='new directory; existing output is never overwritten')
    args = parser.parse_args()
    try:
        report = prepare_code(*(p.expanduser().resolve() for p in (args.base, args.update, args.keys, args.output)))
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(dict(status=report['status'], playable=False, report=str(args.output / 'code-report.json'))))


if __name__ == '__main__':
    main()
