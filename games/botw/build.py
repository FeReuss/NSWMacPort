"""BOTW 1.9.0 build orchestration; all game bytes come from the supplied local inputs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

def reuse_data(tree, source, build):
    """Only immutable verified data is reused; no runtime, tables or executable is copied."""
    expected = build.BOTW_EXPECTED['romfs']
    folder = source / 'extracted/v0/romfs'
    index = folder / 'index.json'
    image = folder / 'romfs.bin'
    report = json.loads(index.read_text())
    table_hash = hashlib.sha256(json.dumps(report['files'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if (report.get('status') != 'pass' or image.stat().st_size != expected['size'] or
            report['romfs_sha256'] != expected['sha256'] or table_hash != expected['files_sha256'] or
            build.sha256(image) != expected['sha256']):
        raise build.BuildError('cached BOTW RomFS or file table failed verification')
    target = tree / 'extracted/v0/romfs'
    if target.resolve() == folder.resolve():
        return
    target.mkdir(parents=True, exist_ok=True)
    for name in ('romfs.bin', 'index.json'):
        build.clone(folder / name, target / name)


def extract_dlc(tree, dlc, keys, logs, build):
    """Both add-on content packs, extracted and verified once and reused while they match add-on content 3.0."""
    expected = build.BOTW_EXPECTED['aoc']
    folder = tree / 'extracted/aoc'

    def valid():
        try:
            report = json.loads((folder / 'index.json').read_text())
            files = hashlib.sha256(json.dumps(report['files'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            mounted = expected['contents'][expected['mounted']]
            return (report.get('status') == 'pass' and files == expected['files_sha256'] and
                    all(report['contents'][t]['romfs_sha256'] == c['romfs_sha256'] for t, c in expected['contents'].items()) and
                    (folder / 'romfs.bin').stat().st_size == mounted['romfs_size'] and
                    build.sha256(folder / 'romfs.bin') == mounted['romfs_sha256'])
        except (OSError, ValueError, KeyError, TypeError):
            return False
    if valid():
        return folder
    if folder.exists():
        shutil.rmtree(folder)
    hactool = build.build_hactool(tree, logs)
    build.say('2/4 DLC: extracting both packs and verifying all IVFC levels')
    build.run([tree / '.venv/bin/python', 'scripts/extract_aoc_romfs.py', *dlc, '--keys', keys, '--hactool', hactool,
               '--output', folder], logs / 'aoc-romfs.log', cwd=tree)
    if not valid():
        raise build.BuildError(f'the DLC does not match BOTW add-on content {expected["version"]} (see {logs / "aoc-romfs.log"})')
    return folder


def build_botw(args, build):
    if args.platform != 'macos':
        raise build.BuildError('BOTW currently supports macOS builds only')
    game, keys = args.game.expanduser().resolve(), args.keys.expanduser().resolve()
    update = args.update.expanduser().resolve() if args.update else None
    if not game.is_file() or build.input_kind(game) not in ('nsp', 'xci'):
        raise build.BuildError('BOTW needs an uncompressed base NSP or XCI')
    if not update or not update.is_file() or build.input_kind(update) not in ('nsp', 'nsz'):
        raise build.BuildError('BOTW needs --update with the separately dumped 1.9.0 NSP/NSZ')
    if not keys.is_file():
        raise build.BuildError(f'no such key file: {keys}')
    dlc = [p.expanduser().resolve() for p in args.dlc]
    for path in dlc:
        if not path.is_file() or build.input_kind(path) != 'nsp':
            raise build.BuildError(f'--dlc needs an add-on content NSP: {path}')
    if dlc and len(dlc) != 2:
        # main+0x10cc2f8: ListAddOnContent(out, 0, 2) must return 2 entries including index 1, or the DLC stays off
        raise build.BuildError('BOTW enables its DLC only with both packs (01007EF00011F001, the data of both, and '
                               "01007EF00011F002, the Champions' Ballad licence); pass both NSPs with --dlc, or none")
    work = args.work.expanduser().resolve() if args.work else game.parent / 'BOTW_build'
    build.lock_work(work)
    out = args.out.expanduser().resolve() if args.out else game.parent / 'Breath of the Wild.app'
    if out.suffix.lower() != '.app':
        raise build.BuildError('the macOS output must end in .app')
    firmware = args.firmware.expanduser().resolve() if args.firmware else None
    fonts = args.shared_fonts.expanduser().resolve() if args.shared_fonts else None
    if firmware and not firmware.is_dir():
        raise build.BuildError(f'no firmware directory: {firmware}')
    if fonts:
        build.font_report(fonts)
    volume = work
    while not volume.exists():
        volume = volume.parent
    known = build.work_tree(work, 'botw')   # what earlier builds left
    cached = args.reuse_data or (known / 'extracted/v0/romfs/romfs.bin').is_file()
    required_gib = (3 if cached else 36) + (3 if dlc and not (known / 'extracted/aoc/romfs.bin').is_file() else 0)
    if args.shaders_only and not (known / 'build/shader-seed/v0-all/seed-manifest.json').is_file():
        required_gib += 15   # the first complete coverage: ~5.3 GB seed, ~1 GB libraries, ~6.3 GB installed cache (measured)
    if shutil.disk_usage(volume).free < required_gib * 2**30:
        raise build.BuildError(f'BOTW needs about {required_gib} GiB free on the work volume')
    if build.input_kind(game) == 'nsp' and not args.shaders_only and not (firmware or fonts or build.fonts_valid(known / 'extracted/firmware/shared-fonts')):
        raise build.BuildError('NSP builds need --firmware or --shared-fonts')
    py = build.prerequisites(args)
    logs = work / 'logs'
    env = dict(os.environ, PATH=os.pathsep.join(build.TOOL_PATHS + [os.environ.get('PATH', '/usr/bin:/bin')]))
    started = time.monotonic()
    tree = build.prepare_work(work, py, logs, game='botw')
    if args.reuse_data:
        reuse_data(tree, args.reuse_data.expanduser().resolve(), build)
    build.step_code(tree, update, keys, env, logs, title='botw')
    if not args.shaders_only:
        build.step_fonts(tree, game, keys, logs, firmware=firmware, shared_fonts=fonts)
    build.step_update_data(tree, game, update, keys, logs, version='v0', title='botw')
    aoc = extract_dlc(tree, dlc, keys, logs, build) if dlc else None
    seed = build.step_shaders(tree, logs, title='botw', all_variants=args.shaders_only, aoc=aoc)
    build.step_shader_libs(tree, logs, required=args.shaders_only, seed=seed)
    build.install_shader_cache(tree, logs, title='botw', seed=seed)
    if args.shaders_only:
        return seed
    build.step_binary(tree, game, keys, env, logs, title='botw')
    binary = tree / 'build/totk-native'
    build.run([binary, 'rewritten/v0', 'probe-audio'], logs / 'probe-audio.log', cwd=tree, env=env)
    build.run([binary, 'rewritten/v0', 'probe'], logs / 'probe-loader.log', cwd=tree, env=env)
    if aoc:   # the add-on content mount and list the game reads, on this build's runtime and image
        build.run([binary, 'rewritten/v0', 'probe-fs'], logs / 'probe-fs.log', cwd=tree, env=env)
    # The source hash binds the runtime patches to this build, rather than to an experimental executable.
    sources = {str(p.relative_to(tree)): build.sha256(p) for folder in ('runtime', 'app', 'third_party/d2r_gfx')
               for p in (tree / folder).rglob('*') if p.is_file()}
    report = dict(schema=1, game='BOTW', version='1.9.0', title_id='01007EF00011E000',
                  patches=['botw-real-time-vfr', 'queued-audio-eos', 'nvn-depth-range', 'stable-shader-identity'],
                  source_sha256=sources, binary_sha256=build.sha256(binary),
                  romfs_sha256=build.BOTW_EXPECTED['romfs']['sha256'],
                  dlc=sorted(build.BOTW_EXPECTED['aoc']['contents']) if aoc else [])
    (tree / 'build/build-manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    build.step_app(tree, out, logs, title='botw', aoc=aoc)
    launcher = out / 'Contents/MacOS/BOTW'
    build.run([launcher, '--check'], logs / 'check-app.log')
    build.run([launcher, '--prepare-data'], logs / 'player-data.log')
    # The packaged image, as the launcher will open it: the player's first start then reuses this verification record
    # instead of hashing 15 GB (8 s), and the DLC mount is checked on the bundle itself.
    data = Path((logs / 'player-data.log').read_text().strip().splitlines()[-1])
    build.run([out / 'Contents/Resources/botw/build/totk-native', 'rewritten/v0', 'probe-fs'], logs / 'probe-fs-app.log',
              cwd=out / 'Contents/Resources/botw', env=dict(env, TOTK_ROMFS_PROOF_DIR=str(data / 'Cache')))
    build.run([build.LSREGISTER, '-f', out], logs / 'register-app.log')
    build.say(f'BOTW app ready{" with both DLC packs" if aoc else ""}: {out} ({time.monotonic()-started:.0f} s). Player data: ~/Documents/NSW Games/Breath of the Wild')
    return out
