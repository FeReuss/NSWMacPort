#!/usr/bin/env python3
"""The app icon from the game's own control data: the 13.0.5 update NSP's control NCA carries icon_<language>.dat (a
256x256 JPEG). package_native_app.py turns it into SSBUIcon.icns. The icon is game-derived: it is written only under an
ignored *_build folder of games/ssbu/13.0.5_online, never committed.

    extract_app_icon.py <13.0.5 update NSP> --keys <prod.keys> [--output SSBU_build/app-assets/SSBUIcon.jpg]
"""
import argparse
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / 'third_party/totk-scripts'))
from extract_exefs import read_at, require, container_entries, common_tickets, nca_inventory
from extract_romfs import index_romfs
from extract_shared_fonts import Slice, load_keys, nca_romfs

TITLE = '01006A800016E000'
LANGUAGES = ['AmericanEnglish', 'BritishEnglish', 'Japanese', 'French', 'German', 'LatinAmericanSpanish', 'Spanish',
             'Italian', 'Dutch', 'CanadianFrench', 'Portuguese', 'Russian', 'Korean', 'TraditionalChinese', 'SimplifiedChinese']


def control_nca(game, keys):
    with game.open('rb') as f:
        _, entries = container_entries(f, game.stat().st_size)
        tickets = common_tickets(f, entries)
        controls = [r for r, _ in nca_inventory(f, entries, keys) if r['content_type'] == 2 and r['title_id'].upper() == TITLE]
        require(len(controls) == 1, 'Expected one SSBU control NCA in the update NSP')
        return controls[0]['nca_offset'], tickets


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('game', type=Path)
    ap.add_argument('--keys', type=Path, required=True)
    ap.add_argument('--output', type=Path, default=HERE / 'SSBU_build/app-assets/SSBUIcon.jpg')
    a = ap.parse_args()
    out = a.output.resolve()
    require(any(p.parent == HERE.resolve() and p.name.endswith('_build') for p in out.parents),
            'Icon output must stay under an ignored *_build folder of games/ssbu/13.0.5_online')
    keys = load_keys(a.keys)
    with tempfile.TemporaryDirectory() as t:
        image = Path(t) / 'control.romfs'
        offset, tickets = control_nca(a.game, keys)
        nca_romfs(Slice(a.game, offset), keys, image, tickets)
        entries = {e['path']: e for e in index_romfs(image)}
        icon = next((entries[f'icon_{lang}.dat'] for lang in LANGUAGES if f'icon_{lang}.dat' in entries), None)
        require(icon is not None, 'no icon in the control data')
        with image.open('rb') as f:
            data = read_at(f, icon['offset'], icon['size'])
    require(data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'), 'Control icon is not a complete JPEG')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(out)


if __name__ == '__main__':
    main()
