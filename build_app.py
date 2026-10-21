#!/usr/bin/env python3
"""MacNativeGames: turn your own XCI or NSP into a native Mac app or iPhone/iPad IPA.

    python3 build_app.py <game.xci|game.nsp> -k <prod.keys> [--platform macos|ios] [-o <output>] [--work <folder>]

Supported: TOTK base 1.0.0 (0100F2C0115B6000), XCI or NSP; optional --update with a supported cumulative update.
NSPs need common tickets (unless rights-free) and a separate shared-font source. iOS builds are unsigned IPAs for sideloading and require Xcode 26+, xcodegen, CMake, and network access to build device libraries.
Nothing from the game is in this repository; everything is made from your files on your Mac. Key values are never printed.

Steps (each one checked against games/totk/assets/expected-v<version>.json, so a damaged dump or another version stops the
build at the first wrong file; a different genuine dump of the same version is accepted — main.npdm by Nintendo's ACID
signature, shared fonts by valid BFTTF structure):
  1. code: ExeFS out of the XCI or NSP, the three code modules rewritten for Apple silicon (games/totk/third_party/d2r)
  2. data: RomFS (17 GB), the system shared fonts (from XCI firmware or an explicit font source), the RomFS overlays
  3. shaders: SPIRV-Cross (fetched at a pinned commit, compiled with clang) and the shader translator, which ships
     ready-made as a native program (games/totk/prebuilt/macos-arm64: vendor/ryujinx + patches + Service.cs, made with
     games/totk/tools/totk_xlate/publish_native.sh); startup shaders are extracted from your selected RomFS and
     translated into a bundled seed, ready for GPU warm-up even with an empty cache on the first launch
  4. the game binary and either a macOS app bundle or an unsigned iPhone/iPad IPA (large game data is cloned when possible);
     for the Mac the startup shaders are then compiled once into this Mac's Metal cache (about 2 minutes on an M4), so the
     first start does not compile them in the background (games/totk/tools/warm_metal_cache.m)

Every intermediate file is in the work folder (default: "<game file folder>/TOTK_build"); a second run reuses
what is already done and correct. Mac saves, shaders, screenshots and logs go to ~/Documents/NSW Games/<game>; iOS keeps saves
and the writable shader cache in the app's Documents container.

Needs for Mac: macOS 26 or later on Apple silicon, the Xcode command line tools, Homebrew with
    brew install opus libvpx python@3.14
For iPhone/iPad: Xcode 26+ with iOS/Metal toolchains, plus xcodegen, CMake and Ninja.
Optional: Xcode's Metal toolchain (xcodebuild -downloadComponent MetalToolchain) compiles the startup shaders into the
app, so moving or rebuilding it does not repeat that work at launch.
"""
import argparse, concurrent.futures, contextlib, fcntl, hashlib, json, os, platform, shutil, signal, struct, subprocess, sys, threading, time
from pathlib import Path

REPO = Path(__file__).resolve().parent
GAME = REPO / 'games/totk'
EXPECTED = json.loads((GAME / 'assets/expected-v1.0.0.json').read_text())
UPDATED = json.loads((GAME / 'assets/expected-v1.1.2.json').read_text())
UPDATED121 = json.loads((GAME / 'assets/expected-v1.2.1.json').read_text())
UPDATED143 = json.loads((GAME / 'assets/expected-v1.4.3.json').read_text())
VERSIONS = {'v0': EXPECTED, 'v112': UPDATED, 'v121': UPDATED121, 'v143': UPDATED143}
BOTW_EXPECTED = json.loads((REPO / 'games/botw/assets/expected-v1.9.0.json').read_text())
UPDATES = ('v112', 'v121', 'v143')
HACTOOL = ('https://github.com/SciresM/hactool.git', '1d64a83450e025622f3468c28fc4164dad2c5ef6')
APP_NAME = 'Tears of the Kingdom'
BUNDLE_ID = 'org.macnativegames.totk'
LSREGISTER = '/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister'
# Pinned upstream sources fetched at build time (repository, commit)
SPIRV_CROSS = ('https://github.com/KhronosGroup/SPIRV-Cross.git', 'aa217aeb6c9f0ace7a0ab233b28807edf45eb165')
# The spirv-cross program: the sources and flags of SPIRV-Cross's own CMake Release build of it, so no cmake is needed
SPIRV_CROSS_SOURCES = ('main.cpp', 'spirv_cfg.cpp', 'spirv_cpp.cpp', 'spirv_cross.cpp', 'spirv_cross_parsed_ir.cpp',
                       'spirv_cross_util.cpp', 'spirv_glsl.cpp', 'spirv_hlsl.cpp', 'spirv_msl.cpp', 'spirv_parser.cpp',
                       'spirv_reflect.cpp')
SPIRV_CROSS_FLAGS = ('-O3', '-DNDEBUG', '-std=c++11', '-arch', 'arm64', '-Wno-deprecated-declarations')
TOOL_PATHS = ['/opt/homebrew/bin', '/usr/local/bin']

# Nintendo's public ACID signing key (generation 0): a dump's main.npdm is genuine when this key verifies
# its ACID signature, which is the check the Horizon loader itself makes before it starts a title
# (Atmosphere libraries/libstratosphere/source/fssystem/fssystem_crypto_configuration.cpp).
NINTENDO_ACID_MODULUS = bytes.fromhex(
    'DDC8DDF24E6DF0CA9EC75DC77BADFE7D238969B6F206A20288E15591ABCB4D50'
    '2EFC9D9476D64CD8FF10FA5E930AB457AC51C71666F41A54C2C5043D1BFE3020'
    '8AAC6F6FF5C7B668B8C9406B42AD1121E78BE9750186E4489B0A0AF87FE887F2'
    '8201E6A30FE466AE833F4E9F5E0130A400B99AAE5F03CC1860E5EF3B5E1516FE'
    '1C8278B52F477C0666885D35A2672010E76C4368D3E45A682A5AE26D73B03153'
    '1C200944F51A9D22BE12A17711E2A1CD409AA28B609BEFA0D34863A2F8A32C08'
    '56522E6019675AA79FDC3F3F692B316AB7884A148480333C9D44B73F4CE175EA'
    '37EAE81E7C77B7C61AA2F09F1061CD7B5B324C37EFB17168530AED517D3522FD'
)


class BuildError(Exception):
    pass


def say(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def run(cmd, log, cwd=None, env=None, quiet=True):
    """Run a step with its whole output in log; on failure show the end of it. quiet: a line a minute instead of the output."""
    cmd = [str(c) for c in cmd]
    log.parent.mkdir(parents=True, exist_ok=True)
    done = threading.Event()

    def beat():
        t0 = time.time()
        while not done.wait(60):
            say(f'   ... still running ({(time.time() - t0) / 60:.0f} min)')
    if quiet:
        threading.Thread(target=beat, daemon=True).start()
    def terminate(_signum, _frame):
        raise KeyboardInterrupt
    old_term = signal.signal(signal.SIGTERM, terminate) if threading.current_thread() is threading.main_thread() else None
    p = None
    try:
        with open(log, 'w') as f:
            p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
            p.wait()
    except BaseException:
        if p is not None and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
            except ProcessLookupError:
                p.wait()
        raise
    finally:
        done.set()
        if old_term is not None:
            signal.signal(signal.SIGTERM, old_term)
    if p.returncode:
        tail = log.read_text(errors='replace').splitlines()[-25:]
        raise BuildError(f'{Path(cmd[1] if Path(cmd[0]).name.startswith("python") else cmd[0]).name} failed (exit {p.returncode}); '
                         f'full output: {log}\n' + '\n'.join(tail))


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(8 << 20):
            h.update(chunk)
    return h.hexdigest()


def check(what, got, want):
    if got != want:
        raise BuildError(f'{what}: SHA-256 {got[:16]}... is not the expected {want[:16]}... '
                         f'(is this Tears of the Kingdom 1.0.0, and the dump complete?)')




# ---- genuine dumps ----
def _mgf1_sha256(seed, length):
    blocks = b''
    for counter in range((length + 31) // 32):
        blocks += hashlib.sha256(seed + counter.to_bytes(4, 'big')).digest()
    return blocks[:length]


def _pss_sha256_valid(signature, modulus, message):
    """RSA-2048 PSS/SHA-256 (32-byte salt, MGF1-SHA256), the scheme of an ACID block's signature."""
    size = len(modulus)
    if size < 256 or len(signature) != size:
        return False
    modulus_number = int.from_bytes(modulus, 'big')
    number = int.from_bytes(signature, 'big')
    if number >= modulus_number:
        return False
    encoded = pow(number, 0x10001, modulus_number).to_bytes(size, 'big')
    if encoded[-1] != 0xBC:
        return False
    hashed, mask = encoded[size - 33:size - 1], _mgf1_sha256(encoded[size - 33:size - 1], size - 33)
    db = bytes([(encoded[0] ^ mask[0]) & 0x7F]) + bytes(a ^ b for a, b in zip(encoded[1:size - 33], mask[1:]))
    if db[:-33].strip(b'\x00') or db[-33] != 0x01:
        return False
    return hashlib.sha256(b'\x00' * 8 + hashlib.sha256(message).digest() + db[-32:]).digest() == hashed


def npdm_signed(path):
    """True when the npdm's ACID block carries a valid Nintendo signature over its own access control.

    The pinned SHA-256 identifies the reference dump; the signature identifies every genuine dump of the
    version, which is why this is the one ExeFS file that does not have to match byte for byte.
    """
    data = path.read_bytes()
    if len(data) < 0x80 or data[:4] != b'META' or int.from_bytes(data[0x04:0x08], 'little') != 0:
        return False
    acid, acid_size = struct.unpack_from('<II', data, 0x78)
    if acid_size < 0x200 or acid + acid_size > len(data) or data[acid + 0x200:acid + 0x204] != b'ACID':
        return False
    size = struct.unpack_from('<I', data, acid + 0x204)[0]
    if size < 0x100 or acid + 0x100 + size > len(data):
        return False
    return _pss_sha256_valid(data[acid:acid + 0x100], NINTENDO_ACID_MODULUS, data[acid + 0x100:acid + 0x100 + size])


def exefs_matches(exefs, expected):
    """True when the extracted ExeFS holds the expected modules; a signed main.npdm of another dump is accepted."""
    for name, digest in expected.items():
        path = exefs / name
        if not path.is_file() or (sha256(path) != digest and not (name == 'main.npdm' and npdm_signed(path))):
            return False
    return True


def _bfttf_valid(path):
    """A structurally valid shared font, by the same size check scripts/extract_shared_fonts.py makes."""
    data = path.read_bytes()
    if len(data) < 12:
        return False
    magic, size = struct.unpack_from('>II', data)
    key = magic ^ 0x7f9a0218
    if (size ^ key) + 8 != len(data):
        return False
    return struct.unpack_from('>I', data, 8)[0] ^ key in (0x00010000, 0x4F54544F)


# ---- prerequisites ----
def prerequisites(args):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise BuildError('needs a Mac with Apple silicon')
    major = int(platform.mac_ver()[0].split('.')[0] or 0)
    if major < 26:
        raise BuildError(f'needs macOS 26 or later (Metal 4); this is macOS {platform.mac_ver()[0]}')
    if subprocess.run(['xcrun', '--find', 'clang'], capture_output=True).returncode:
        raise BuildError('the Xcode command line tools are missing: xcode-select --install')
    # SPIRV-Cross is C++: without the SDK's libc++ headers the build stops much later at "'string' file not found" (#41)
    cxx = subprocess.run(['xcrun', 'clang++', '-std=c++11', '-x', 'c++', '-fsyntax-only', '-'], input='#include <string>\n',
                         capture_output=True, text=True)
    if cxx.returncode:
        raise BuildError('clang++ finds no C++ standard library headers, so the developer tools or their SDK are incomplete. '
                         'With Xcode: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer; with the command line '
                         'tools only: sudo rm -rf /Library/Developer/CommandLineTools && xcode-select --install '
                         f'(xcode-select -p and xcrun --show-sdk-path show what is used). clang++ said: {cxx.stderr.strip()[:300]}')
    brew = Path('/opt/homebrew')
    missing = [p for p in ('opus', 'libvpx') if not (brew / 'opt' / p / 'lib').is_dir()]
    if missing:
        raise BuildError(f'Homebrew packages missing: brew install {" ".join(missing)}')
    if not shutil.which('git', path=os.pathsep.join(TOOL_PATHS + ['/usr/bin'])):
        raise BuildError('git not found: xcode-select --install')
    py = next((p for p in (shutil.which(n, path=os.pathsep.join(TOOL_PATHS)) for n in ('python3.14', 'python3.15')) if p), None)
    if not py:
        raise BuildError('Python 3.14 not found (the build reads zstd with the standard library): brew install python@3.14')
    return Path(py)


# ---- work folder ----
def git_fetch(url, commit, dst, log):
    if (dst / '.git').is_dir() and subprocess.run(['git', '-C', dst, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip() == commit:
        return
    if dst.is_symlink():
        dst.unlink()   # replace only the cache link, never its external target
    elif dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    run(['git', '-C', dst, 'init', '-q'], log)
    run(['git', '-C', dst, 'fetch', '-q', '--depth', '1', url, commit], log)
    run(['git', '-C', dst, 'checkout', '-q', 'FETCH_HEAD'], log)


def link(path, target):
    if path.is_symlink() or path.exists():
        if path.is_symlink() and path.resolve() == Path(target).resolve():
            return
        path.unlink() if path.is_symlink() else shutil.rmtree(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)


def work_tree(work, game):
    """work/<game>.noindex, the folder of the generated files: Spotlight skips *.noindex folders (app/game_storage.h), and a
    build writes ~500,000 files here that it would re-index for hours while the game runs. Called under lock_work: a work
    folder of earlier builds is renamed, keeping its extracted data and caches (the venv is outside it, in work/venv)."""
    tree = work / f'{game}.noindex'
    if not tree.exists() and (work / game).is_dir():
        (work / game).rename(tree)
    return tree


def prepare_work(work, py, logs, *, game="totk"):
    """work/totk.noindex: the game folder of this repository with the generated files inside it (the build scripts write
    extracted/, rewritten/, build/ and analysis/ next to themselves); its tooling/ points at the fetched sources."""
    tree = work_tree(work, game)
    tree.mkdir(parents=True, exist_ok=True)
    # Match both directories AND symlinks to directories. A trailing slash
    # allows rsync -a to copy a tooling symlink from a development checkout.
    excludes = [f'--exclude=/{name}' for name in ('extracted', 'rewritten', 'build', 'analysis', '.venv', 'tooling', 'user-data')]
    run(['rsync', '-a', *excludes, f'{GAME}/', f'{tree}/'], logs / 'sources.log')
    if game == 'botw':
        shutil.copy2(REPO / 'games/botw/game_patches.c', tree / 'runtime/game_patches.c')
    tooling = tree / 'tooling'
    # Migrate a work folder made by the old copier before writing child links:
    # link(tooling/spirv-cross, ...) must not traverse into a shared checkout.
    if tooling.is_symlink():
        tooling.unlink()
    tooling.mkdir(exist_ok=True)
    src = work / 'src'
    say('fetching the pinned SPIRV-Cross sources')
    git_fetch(*SPIRV_CROSS, src / 'SPIRV-Cross', logs / 'fetch-spirv-cross.log')
    link(tooling / 'spirv-cross', src / 'SPIRV-Cross')
    venv = work / 'venv'
    if not (venv / 'bin/python').exists():
        say('Python environment for the build tools')
        run([py, '-m', 'venv', venv], logs / 'venv.log')
    run([venv / 'bin/python', '-m', 'pip', 'install', '-q', '-r', tree / 'requirements.txt'], logs / 'pip.log')
    link(tree / '.venv', venv)
    (tree / 'analysis').mkdir(exist_ok=True)
    (tree / 'build').mkdir(exist_ok=True)
    return tree


# ---- steps ----
def input_kind(game):
    with game.open('rb') as f:
        if f.read(4) == b'PFS0':
            return 'nsz' if game.suffix.lower() == '.nsz' else 'nsp'
        f.seek(0x100)
        if f.read(4) == b'HEAD':
            return 'xci'
    raise BuildError('expected an XCI or NSP (PFS0) game file, or a supported NSZ update')


def source_matches(manifest, game):
    try:
        data = json.loads(manifest.read_text())
        stat = game.stat()
        return (data['source'] == str(game.resolve()) and data['source_size'] == stat.st_size
                and data['source_mtime_ns'] == stat.st_mtime_ns)
    except (OSError, ValueError, KeyError):
        return False


def prepare_update_container(tree, game, keys, logs):
    """Expand and cache a compressed update using NSZ's hash-verified NCA conversion."""
    if game.suffix.lower() != '.nsz':
        return game
    folder = tree / 'extracted/containers'
    output = folder / f'{game.stem}.nsp'
    manifest = folder / f'{game.stem}.json'
    if source_matches(manifest, game) and output.is_file():
        return output
    folder.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    if manifest.exists():
        manifest.unlink()
    say('1/4 code: decompressing the NSZ update and verifying its NCA hashes')
    run([tree / '.venv/bin/nsz', '-D', '--keys', keys, '-o', folder, game],
        logs / f'decompress-{game.stem}.log', cwd=tree)
    generated = folder / f'{game.stem}.nsp'
    if not generated.is_file():
        raise BuildError(f'NSZ decompressor did not produce an NSP: {generated}')
    if generated != output:
        generated.replace(output)
    stat = game.stat()
    manifest.write_text(json.dumps(dict(source=str(game.resolve()), source_size=stat.st_size,
                                        source_mtime_ns=stat.st_mtime_ns), indent=2) + '\n')
    return output


def identify_update(tree, game, keys, logs):
    """Choose the runtime by verified module bytes, never by the package filename."""
    source = prepare_update_container(tree, game, keys, logs)
    for version in UPDATES:
        exefs = tree / f'extracted/{version}/exefs'
        if source_matches(exefs.parent / 'extraction-manifest.json', source) and exefs_matches(
                exefs, VERSIONS[version]['exefs_sha256']):
            return version, source
    staging = tree / 'extracted/update-detection'
    if staging.exists(): shutil.rmtree(staging)
    run([tree / '.venv/bin/python', 'scripts/extract_exefs.py', source, '--keys', keys,
         '--update', '--output', staging / 'exefs'], logs / 'identify-update.log', cwd=tree)
    version = next((version for version in UPDATES
                    if exefs_matches(staging / 'exefs', VERSIONS[version]['exefs_sha256'])), None)
    if version is None:
        raise BuildError('unsupported update module hashes; expected 1.1.2, 1.2.1 or 1.4.3 (the file name is not used to choose a version)')
    target = tree / f'extracted/{version}'
    target.mkdir(parents=True, exist_ok=True)
    if (target / 'exefs').exists(): shutil.rmtree(target / 'exefs')
    (staging / 'exefs').rename(target / 'exefs')
    (staging / 'extraction-manifest.json').replace(target / 'extraction-manifest.json')
    staging.rmdir()
    say(f'update identified by verified ExeFS hashes: {VERSIONS[version]["display_version"]}')
    return version, source


def font_report(folder):
    """The fonts bound into the runtime: the pinned hashes identify the reference dump, and a valid font from
    another firmware version is bound by the SHA-256 of its actual bytes (the manifest keeps that value, so
    scripts/prepare_shared_fonts.py writes the real bytes into the runtime)."""
    rows = []
    for kind, (name, expected) in enumerate(EXPECTED['shared_fonts_sha256'].items()):
        path = folder / f'{kind}-{name}.bfttf'
        if not path.is_file():
            raise BuildError(f'missing shared font: {path}')
        digest = sha256(path)
        if digest != expected:
            if not _bfttf_valid(path):
                raise BuildError(f'shared font {name}: not the expected bytes and not a valid BFTTF font '
                                 f'(SHA-256 {digest[:16]}...)')
            say(f'2/4 data: shared font {name} comes from another firmware version; its bytes are bound instead')
        size = path.stat().st_size
        rows.append(dict(type=kind, name=name, bfttf_bytes=size, font_bytes=size - 8, sha256=digest))
    return dict(status='pass', integrity='Each shared font is a valid BFTTF; the reference dump matches its pinned SHA-256',
                fonts=rows)


def validate_fonts(folder):
    report = font_report(folder)
    try:
        manifest = json.loads((folder / 'manifest.json').read_text())
        rows = manifest['fonts']
        if manifest['status'] != 'pass' or len(rows) != 6 or any(
                any(row.get(k) != value for k, value in expected.items())
                for row, expected in zip(rows, report['fonts'])):
            raise ValueError('font manifest differs from its files')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise BuildError(f'invalid shared-font manifest in {folder}: {e}') from None


def fonts_valid(folder):
    try:
        validate_fonts(folder)
        return True
    except (BuildError, OSError):
        return False


def step_code(tree, game, keys, env, logs, *, version="v0", title="totk"):
    expected = BOTW_EXPECTED if title == 'botw' else VERSIONS[version]
    update_args = ["--update"] if title == 'botw' or version != 'v0' else []
    title_args = ['--title-id', '0x01007ef00011e000'] if title == 'botw' else []
    py = tree / '.venv/bin/python'
    exefs = tree / f'extracted/{version}/exefs'
    want = expected['exefs_sha256']
    manifest = exefs.parent / 'extraction-manifest.json'
    if not source_matches(manifest, game) or not exefs_matches(exefs, want):
        say('1/4 code: extracting the ExeFS from the game file')
        if exefs.exists():
            shutil.rmtree(exefs)
        source = prepare_update_container(tree, game, keys, logs) if update_args else game
        run([py, 'scripts/extract_exefs.py', source, '--keys', keys, '--output', exefs, *update_args, *title_args], logs / f'extract_exefs-{version}.log', cwd=tree)
    for n, h in want.items():
        if not (exefs / n).is_file():
            raise BuildError(f'ExeFS {n} is missing; not the supported version')
        got = sha256(exefs / n)
        if got == h:
            continue
        if n == 'main.npdm' and npdm_signed(exefs / n):
            say(f"1/4 code: main.npdm is a Nintendo-signed {expected['display_version']} file from another dump; using it")
            continue
        check(f'ExeFS {n}', got, h)
    if not (tree / f'extracted/{version}/elf/main-inventory.json').exists():
        run([py, 'scripts/prepare_modules.py', exefs, '--output', tree / f'extracted/{version}/elf'], logs / 'prepare_modules.log', cwd=tree)
    out = tree / f'rewritten/{version}'
    out.mkdir(parents=True, exist_ok=True)
    for name in ('sdk', 'subsdk0', 'main'):
        image, h = out / f'{name}.patched.bin', expected['rewritten_sha256'][f'{name}.patched.bin']
        if image.exists() and sha256(image) == h:
            continue
        say(f'1/4 code: rewriting {name} for Apple silicon' + (' (the big one: about a minute)' if name == 'main' else ''))
        run([py, 'third_party/d2r/patch_nso.py', exefs / name, out, name], logs / f'rewrite_{version}_{name}.log', cwd=tree,
            env=dict(env, DM_RULESET='native', DM_PRIVATE_GATES='0'))
        check(f'rewritten {name}', sha256(image), h)
    # The loader binds itself to these images (scripts/prepare_runtime.py): they are the same bytes as the rewrite that
    # was verified site by site (python3 games/totk/scripts/verify_rewrites.py re-runs that verification, hours).
    report = dict(format=1, status='pass', rom_version=version, scope=f'hash match with verified {expected["display_version"]} rewrite',
                  module_file_sha256={**{f'extracted/{version}/exefs/{n}': want[n] for n in ('main', 'sdk', 'subsdk0')},
                                      **{f'rewritten/{version}/{n}.patched.bin': expected['rewritten_sha256'][f'{n}.patched.bin'] for n in ('main', 'sdk', 'subsdk0')}})
    report_path = tree / ('analysis/phase12-validation.json' if version == 'v0' else f'analysis/{version}-port/phase12-validation.json')
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + '\n')


def step_fonts(tree, game, keys, logs, *, firmware=None, shared_fonts=None):
    py = tree / '.venv/bin/python'
    fonts = tree / 'extracted/firmware/shared-fonts'
    if not fonts_valid(fonts):
        if shared_fonts:
            # Validate the actual bytes before copying, not just the source's manifest.
            report = font_report(shared_fonts)
            if shared_fonts.resolve() != fonts.resolve():
                if fonts.exists():
                    shutil.rmtree(fonts)
                fonts.mkdir(parents=True)
                for f in report['fonts']:
                    name = f"{f['type']}-{f['name']}.bfttf"
                    shutil.copy2(shared_fonts / name, fonts / name)
            (fonts / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
        else:
            if not firmware and input_kind(game) == 'nsp':
                raise BuildError('NSP needs --firmware or --shared-fonts because no valid shared fonts are cached')
            say('2/4 data: extracting the system shared fonts')
            if fonts.exists():
                shutil.rmtree(fonts)
            source_args = ['--firmware', firmware] if firmware else ['--xci', game]
            run([py, 'scripts/extract_shared_fonts.py', '--keys', keys, *source_args, '--output', fonts],
                logs / 'shared_fonts.log', cwd=tree)
        validate_fonts(fonts)


def step_romfs(tree, keys, logs):
    py = tree / '.venv/bin/python'
    romfs = tree / 'extracted/v0/romfs'
    index = romfs / 'index.json'
    ok = index.exists() and (romfs / 'romfs.bin').is_file() and json.loads(index.read_text()).get('romfs_sha256') == EXPECTED['romfs']['sha256'] and \
        (romfs / 'romfs.bin').stat().st_size == EXPECTED['romfs']['size']
    if not ok:
        say('2/4 data: extracting and verifying the RomFS (17 GB, a minute or two)')
        if romfs.exists():
            shutil.rmtree(romfs)
        run([py, 'scripts/extract_romfs.py', '--keys', keys], logs / 'extract_romfs.log', cwd=tree)
        r = json.loads(index.read_text())
        check('RomFS', r['romfs_sha256'], EXPECTED['romfs']['sha256'])


def step_data(tree, game, keys, logs, *, firmware=None, shared_fonts=None):
    py = tree / ".venv/bin/python"
    step_romfs(tree, keys, logs)
    step_fonts(tree, game, keys, logs, firmware=firmware, shared_fonts=shared_fonts)
    if not (tree / 'extracted/v0/romfs-patches/manifest.tsv').exists():
        say('2/4 data: RomFS overlays (quick-menu capture timing, game-over retry)')
        run([py, 'scripts/make_romfs_patches.py'], logs / 'romfs_patches.log', cwd=tree)
    if not (tree / 'extracted/v0/romfs-patches-out/2560x1440/manifest.tsv').exists():
        say('2/4 data: RomFS overlay for the 2560x1440 output (key 3 in the game)')
        run([py, 'scripts/make_output_res_overlay.py', '2560x1440'], logs / 'output_res_overlay.log', cwd=tree)


def build_hactool(tree, logs):
    source = tree.parent / 'src/hactool'
    git_fetch(*HACTOOL, source, logs/'fetch-hactool.log')
    # Add an explicit private title-key file, so base NSP and update tickets do not
    # need command-line keys or changes to the player's HOME/.switch files.
    main = source/'main.c'
    text = main.read_text()
    old = 'FILE *titlekeyfile = open_key_file("title");'
    new = 'FILE *titlekeyfile = getenv("MNG_TITLE_KEYS") ? fopen(getenv("MNG_TITLE_KEYS"), "rb") : open_key_file("title");'
    if new not in text:
        if text.count(old) != 1:
            raise BuildError('pinned hactool title-key hook changed')
        main.write_text(text.replace(old, new))
    # Expose only the bounded Program NCA inside the original XCI/NSP as a
    # seekable FILE. This avoids a second 17 GB base copy during BKTR merging.
    text = main.read_text()
    if '#define _DARWIN_C_SOURCE 1' not in text:
        text = '#define _DARWIN_C_SOURCE 1\n' + text
        main.write_text(text)
    hook = 'static FILE *mng_open_base(const char *path)'
    if hook not in text:
        helper = r'''
typedef struct { FILE *file; off_t start, size, position; } mng_base_range;
static int mng_base_read(void *cookie, char *buffer, int count) {
    mng_base_range *range = cookie;
    off_t left = range->size - range->position;
    if (left < count) count = (int)left;
    if (!count) return 0;
    if (fseeko(range->file, range->start + range->position, SEEK_SET)) return -1;
    int got = (int)fread(buffer, 1, count, range->file);
    if (!got && ferror(range->file)) return -1;
    range->position += got;
    return got;
}
static fpos_t mng_base_seek(void *cookie, fpos_t offset, int whence) {
    mng_base_range *range = cookie;
    off_t base = whence == SEEK_SET ? 0 : whence == SEEK_CUR ? range->position : whence == SEEK_END ? range->size : -1;
    if (base < 0 || offset < -base || offset > range->size - base) return (fpos_t)-1;
    return (fpos_t)(range->position = base + offset);
}
static int mng_base_close(void *cookie) {
    mng_base_range *range = cookie;
    int result = fclose(range->file); free(range); return result;
}
static FILE *mng_open_base(const char *path) {
    const char *start = getenv("MNG_BASE_RANGE_OFFSET"), *size = getenv("MNG_BASE_RANGE_SIZE");
    if (!start && !size) return fopen(path, "rb");
    if (!start || !size) return NULL;
    char *end1, *end2;
    unsigned long long a = strtoull(start, &end1, 10), n = strtoull(size, &end2, 10);
    if (!*start || !*size || *end1 || *end2 || a > INT64_MAX || !n || n > INT64_MAX - a) return NULL;
    mng_base_range *range = calloc(1, sizeof(*range));
    if (!range) return NULL;
    range->file = fopen(path, "rb"); range->start = (off_t)a; range->size = (off_t)n;
    if (!range->file) { free(range); return NULL; }
    if (fseeko(range->file, 0, SEEK_END) || ftello(range->file) < (off_t)(a + n)) { mng_base_close(range); return NULL; }
    FILE *view = funopen(range, mng_base_read, NULL, mng_base_seek, mng_base_close);
    if (!view) mng_base_close(range);
    return view;
}
'''
        marker = 'int main(int argc, char **argv)'
        if text.count(marker) != 1 or text.count('nca_ctx.tool_ctx->base_file = fopen(optarg, "rb")') != 2:
            raise BuildError('pinned hactool bounded base-file hook changed')
        text = text.replace(marker, helper + '\n' + marker)
        text = text.replace('nca_ctx.tool_ctx->base_file = fopen(optarg, "rb")',
                            'nca_ctx.tool_ctx->base_file = mng_open_base(optarg)')
        main.write_text(text)
    # Some newer cumulative update NCAs leave sector-aligned padding after the
    # BKTR subsection table. The relocation and subsection tables still have to
    # be contiguous and remain inside the section; IVFC verification below
    # validates the reconstructed RomFS data. Keep hactool's strict default for
    # the table boundary and permit only this unused trailing section space.
    nca = source/'nca.c'
    text = nca.read_text()
    old = ('sb->relocation_header.offset + sb->relocation_header.size != sb->subsection_header.offset ||\n'
           '            sb->subsection_header.offset + sb->subsection_header.size != ctx->size')
    new = ('sb->relocation_header.offset + sb->relocation_header.size != sb->subsection_header.offset ||\n'
           '            sb->subsection_header.offset + sb->subsection_header.size > ctx->size ||\n'
           '            (ctx->size - (sb->subsection_header.offset + sb->subsection_header.size)) % 0x200 != 0')
    if new not in text:
        if text.count(old) != 1:
            raise BuildError('pinned hactool BKTR layout check changed')
        nca.write_text(text.replace(old, new))
    text = nca.read_text()
    old = 'if (ctx->bktr_ctx.subsection_block->total_size != sb->subsection_header.offset) {'
    new = ('if (ctx->bktr_ctx.subsection_block->total_size != sb->subsection_header.offset &&\n'
           '            (sb->relocation_header.size > sb->subsection_header.offset ||\n'
           '             ctx->bktr_ctx.subsection_block->total_size != sb->subsection_header.offset - sb->relocation_header.size)) {')
    if new not in text:
        if text.count(old) != 1:
            raise BuildError('pinned hactool BKTR total-size check changed')
        nca.write_text(text.replace(old, new))
    config = source/'config.mk'
    if not config.exists():
        shutil.copy2(source/'config.mk.template', config)
    run(['make', '-j2', 'CC=clang'], logs/'hactool-build.log', cwd=source)
    return source/'hactool'


def step_update_data(tree, game, update, keys, logs, *, version='v112', title='totk'):
    expected = BOTW_EXPECTED if title == 'botw' else VERSIONS[version]
    folder = tree/f'extracted/{version}/romfs'
    index = folder/'index.json'
    valid = False
    if index.is_file() and (folder/'romfs.bin').is_file():
        try:
            report = json.loads(index.read_text())
            valid = (report.get('status') == 'pass' and report['romfs_sha256'] == expected['romfs']['sha256']
                     and (folder/'romfs.bin').stat().st_size == expected['romfs']['size'])
        except (ValueError, KeyError):
            pass
    if not valid:
        hactool = build_hactool(tree, logs)
        if folder.exists():
            shutil.rmtree(folder)
        say(f'2/4 update: merging base + {expected["display_version"]} and verifying all IVFC levels')
        run([tree/'.venv/bin/python', 'scripts/extract_update_romfs.py', game, update,
             '--keys', keys, '--hactool', hactool, '--output', folder,
             *(['--title-id', '0x01007ef00011e000'] if title == 'botw' else [])], logs/'update-romfs.log', cwd=tree)
        report = json.loads(index.read_text())
        check(f'{expected["display_version"]} RomFS', report['romfs_sha256'], expected['romfs']['sha256'])
        check(f'{expected["display_version"]} RomFS size', report['romfs_size'], expected['romfs']['size'])
    if title == 'botw':
        return
    for script, arguments in [('make_romfs_patches.py', []), ('make_output_res_overlay.py', ['2560x1440'])]:
        run([tree/'.venv/bin/python', 'scripts/'+script, *arguments, '--version', version],
            logs/(version+'-'+script+'.log'), cwd=tree)


def build_spirv_cross(tree, logs):
    """The spirv-cross program with clang (the sources and flags of SPIRV-Cross's CMake Release build of it)."""
    src, out = tree / 'tooling/spirv-cross', tree / 'build/spirv-cross'
    objs = out / 'obj'
    objs.mkdir(parents=True, exist_ok=True)

    def compile_one(name):
        run(['xcrun', 'clang++', *SPIRV_CROSS_FLAGS, '-c', src / name, '-o', objs / (name + '.o')],
            logs / 'spirv-cross' / (name + '.log'), quiet=False)
    with concurrent.futures.ThreadPoolExecutor(os.cpu_count() or 4) as pool:
        list(pool.map(compile_one, SPIRV_CROSS_SOURCES))
    run(['xcrun', 'clang++', '-arch', 'arm64', *[objs / (n + '.o') for n in SPIRV_CROSS_SOURCES], '-o', out / 'spirv-cross'],
        logs / 'spirv-cross' / 'link.log', quiet=False)


def step_shaders(tree, logs, *, version='v0', title='totk', all_variants=False, aoc=None):
    """Returns the seed folder. BOTW's complete coverage (build_shaders.py) keeps its own folder and receipts, so an app
    build in between does not replace it and the next complete run resumes instead of translating ~120,000 variants."""
    seed = tree/'build/shader-seed'/(version + ('-all' if all_variants else ''))
    if not (tree / 'build/spirv-cross/spirv-cross').exists():
        say('3/4 shaders: building SPIRV-Cross with clang')
        build_spirv_cross(tree, logs)
    # the shader translation service: a native program in this repository (no .NET needed to build or to play)
    translator = tree / 'prebuilt/macos-arm64/totk_xlate'
    want = (translator.parent / 'totk_xlate.sha256').read_text().strip()
    if not translator.is_file() or sha256(translator) != want:
        raise BuildError(f'{translator}: not the shader translator this repository ships (its SHA-256 differs from '
                         f'totk_xlate.sha256); check it out again, or rebuild it with games/totk/tools/totk_xlate/publish_native.sh')
    say('3/4 shaders: discovering and translating startup shaders from your game data')
    run([tree/'.venv/bin/python', 'scripts/prepare_botw_shaders.py' if title == 'botw' else 'scripts/prepare_shader_seed.py',
         '--romfs', tree/f'extracted/{version}/romfs', '--output', seed,
         '--translator', translator, '--spirv-cross', tree/'build/spirv-cross/spirv-cross',
         '--version', version, '--modules', tree/f'rewritten/{version}', *(['--all-variants'] if all_variants else []),
         *(['--aoc', aoc] if aoc else [])],
        logs/'prepare-shader-seed.log', cwd=tree)
    return seed


def developer_dirs():
    """Developer folders that may hold the Metal toolchain: DEVELOPER_DIR, the one xcode-select points at, Xcode*.app in
    /Applications, then any Xcode Spotlight knows (one kept outside /Applications, for example next to the game)."""
    found = [os.environ['DEVELOPER_DIR']] if os.environ.get('DEVELOPER_DIR') else []
    try:
        found.append(subprocess.check_output(['xcode-select', '-p'], text=True, stderr=subprocess.DEVNULL).strip())
    except (OSError, subprocess.CalledProcessError):
        pass
    found += [str(p / 'Contents/Developer') for p in sorted(Path('/Applications').glob('Xcode*.app'))]
    found += [str(p / 'Contents/Developer') for p in sorted(Path.home().glob('Applications/Xcode*.app'))]
    # An Xcode kept on another volume, at its top or one folder down (Spotlight may not index it)
    found += [str(p / 'Contents/Developer') for p in sorted(Path('/Volumes').glob('*/Xcode*.app'))]
    found += [str(p / 'Contents/Developer') for p in sorted(Path('/Volumes').glob('*/*/Xcode*.app'))]
    try:
        spotlight = subprocess.check_output(['mdfind', "kMDItemCFBundleIdentifier == 'com.apple.dt.Xcode'"], text=True,
                                            stderr=subprocess.DEVNULL)
        found += [line + '/Contents/Developer' for line in spotlight.splitlines() if line.endswith('.app')]
    except (OSError, subprocess.CalledProcessError):
        pass
    return list(dict.fromkeys(d for d in found if d))


def metal_compiler():
    """(compiler, its version, the environment to run it with), or None. Apple's offline shader compiler comes with an
    Xcode's optional Metal toolchain, not the command line tools, so when the selected folder has none the other Xcodes
    on this Mac are tried."""
    for developer in developer_dirs():
        env = dict(os.environ, DEVELOPER_DIR=developer)
        found = subprocess.run(['xcrun', '--find', 'metal'], capture_output=True, text=True, env=env)
        metal = found.stdout.strip()
        if found.returncode or not metal:
            continue
        version = subprocess.run([metal, '--version'], capture_output=True, text=True, env=env)
        if not version.returncode and version.stdout:
            return metal, version.stdout.splitlines()[0], env
    return None


# The options of totk_precise_opts() in nvn_mtl.m: the translated shaders need exactly rounded float arithmetic.
METAL_FLAGS = ['-fmetal-math-mode=safe']


def _shader_atomic_write(path, data):
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f'.{path.name}.', suffix='.partial', dir=path.parent, delete=False) as out:
        temporary = Path(out.name)
        try:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
            out.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _shader_link(library, target):
    """Publish a verified library under its seed name as a hard link. Content-addressed libraries are only ever replaced
    by rename, never rewritten in place, so the names cannot diverge; copies of 147,000 libraries cost 1.3 GB and minutes."""
    temporary = target.with_name(f'.{target.name}.{os.getpid()}.link')
    temporary.unlink(missing_ok=True)
    try:
        os.link(library, temporary)
        temporary.replace(target)
    except OSError:   # another volume or a file system without hard links
        temporary.unlink(missing_ok=True)
        _shader_atomic_write(target, library.read_bytes())


def _shader_metallib_valid(path):
    try:
        with path.open('rb') as stream:
            header = stream.read(24)
        size = path.stat().st_size
        return size >= 88 and len(header) == 24 and header[:4] == b'MTLB' and struct.unpack_from('<Q', header, 16)[0] == size
    except OSError:
        return False


def _shader_library_valid(library, source_digest, compiler):
    try:
        record = json.loads(library.with_suffix('.json').read_text())
        recipe = json.loads(record['compiler'])
        return (record['source_sha256'] == source_digest and recipe['flags'] == METAL_FLAGS and
                (compiler is None or record['compiler'] == compiler) and
                _shader_metallib_valid(library) and record['size'] == library.stat().st_size and
                record['sha256'] == sha256(library))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def step_shader_libs(tree, logs, *, version='v0', required=False, seed=None):
    """Resume only verified source/compiler matches; interrupted units never become published libraries."""
    import signal
    import tempfile
    seed = seed or tree/f'build/shader-seed/{version}'
    sources = sorted(seed.glob('*.metal'))
    found = metal_compiler()
    if not found:
        if required:
            raise BuildError('offline shader compilation needs Apple\'s Metal toolchain: xcodebuild -downloadComponent MetalToolchain')
        say('3/4 shaders: no Metal toolchain (xcodebuild -downloadComponent MetalToolchain), so the startup shaders go '
            "into Metal's cache for the app's location at the end of the build instead; moving the app makes them cold again")
    metal, compiler, metal_env = found or (None, None, {})
    libs = tree/'build/shader-libs'
    stamp = libs/'compiler.txt'
    identity = (json.dumps({'version': compiler, 'path': str(Path(metal).resolve()), 'sha256': sha256(Path(metal)),
                            'flags': METAL_FLAGS, 'developer_dir': metal_env.get('DEVELOPER_DIR')}, sort_keys=True)
                if metal else None)
    libs.mkdir(parents=True, exist_ok=True)
    source_digests = [(src, sha256(src)) for src in sources]
    todo, checked = {}, set()
    for src, digest in source_digests:
        if digest in checked:   # BOTW: 147,022 sources share 67,084 libraries; each is checked once
            continue
        checked.add(digest)
        library = libs/f'{digest}.metallib'
        if not _shader_library_valid(library, digest, identity):
            # Callers use the final name directly; retain stale bytes outside that public name.
            if library.is_file():
                previous = library.with_name(f'{digest}.{sha256(library)}.previous')
                library.replace(previous)
                receipt = library.with_suffix('.json')
                if receipt.is_file():
                    receipt.replace(Path(str(previous) + '.json'))
            todo.setdefault(digest, src)
    say(f'3/4 shaders: compiling {len(todo)} of {len(sources)} startup shaders ahead of time')
    cancelled = threading.Event()
    processes = set()
    process_lock = threading.Lock()

    def compile_one(item):
        digest, src = item
        if metal is None:
            return f'{src.name}\nMetal toolchain unavailable; compile from source at runtime'
        descriptor, name = tempfile.mkstemp(prefix=f'.{digest}.', suffix='.partial', dir=libs)
        os.close(descriptor)
        partial = Path(name)
        try:
            with process_lock:
                if cancelled.is_set():
                    return
                process = subprocess.Popen([metal, *METAL_FLAGS, '-o', partial, src], stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True, env=metal_env, start_new_session=True)
                processes.add(process)
            try:
                stdout, stderr = process.communicate()
            finally:
                with process_lock:
                    processes.discard(process)
            if cancelled.is_set():
                return
            if process.returncode or not _shader_metallib_valid(partial) or sha256(src) != digest:
                return f'{src.name}\n{stdout}{stderr}\ncompiler failed, output incomplete, or source changed'
            record = {'source_sha256': digest, 'compiler': identity, 'sha256': sha256(partial), 'size': partial.stat().st_size}
            library = libs/f'{digest}.metallib'
            with process_lock:
                if cancelled.is_set():
                    return
                partial.replace(library)
                _shader_atomic_write(library.with_suffix('.json'), (json.dumps(record, sort_keys=True) + '\n').encode())
        finally:
            partial.unlink(missing_ok=True)

    def interrupted(number, _frame):
        if not cancelled.is_set():
            cancelled.set()
            raise KeyboardInterrupt(f'Metal compilation interrupted by signal {number}')

    handlers = {}
    if threading.current_thread() is threading.main_thread():
        for number in (signal.SIGINT, signal.SIGTERM):
            handlers[number] = signal.signal(number, interrupted)
    pool = concurrent.futures.ThreadPoolExecutor(os.cpu_count() or 4)
    try:
        futures = [pool.submit(compile_one, item) for item in todo.items()]
        failed = [error for future in concurrent.futures.as_completed(futures) if (error := future.result())]
    except BaseException:
        cancelled.set()
        with process_lock:
            active = list(processes)
        for process in active:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 1
        for process in active:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        for number, previous in handlers.items():
            signal.signal(number, previous)
    log = logs/'shader-libs.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    _shader_atomic_write(log, '\n'.join(sorted(failed)).encode())
    if failed:   # the game compiles these from source at launch, as before
        say(f'   {len(failed)} shaders did not compile ahead of time; they are compiled at launch ({log})')
    usable = {digest for digest in {d for _src, d in source_digests} if _shader_library_valid(libs/f'{digest}.metallib', digest, identity)}
    valid = [(src, digest) for src, digest in source_digests if digest in usable]
    manifest = seed / 'seed-manifest.json'
    if manifest.is_file():
        report = json.loads(manifest.read_text())
        report['metal_compiler'] = identity or 'verified receipts; Metal toolchain unavailable'
        report['precompiled'] = len(valid)
        report['compile_failures'] = len(failed)
        report['files'] = {name: digest for name, digest in report['files'].items() if not name.endswith('.metallib')}
        library_digests = {}
        for src, digest in valid:
            lib = libs/f'{digest}.metallib'
            target = src.with_suffix('.metallib')
            library_digest = library_digests.get(digest) or library_digests.setdefault(digest, sha256(lib))
            # The published link is the library itself (same inode): nothing to read to know its bytes.
            if not (target.is_file() and (os.path.samefile(target, lib) or sha256(target) == library_digest)):
                _shader_link(lib, target)
            report['files'][target.name] = library_digest
        valid_sources = {src for src, _digest in valid}
        for src in sources:
            target = src.with_suffix('.metallib')
            if src not in valid_sources and target.is_file():
                target.replace(target.with_name(f'.{target.name}.{sha256(target)}.previous'))
        _shader_atomic_write(manifest, (json.dumps(report, indent=2) + '\n').encode())
    if identity is not None:
        _shader_atomic_write(stamp, identity.encode())
    if required and not valid:
        raise BuildError(f'no shader libraries compiled successfully; see {log}')



def step_binary(tree, game, keys, env, logs, *, version="v0", title="totk"):
    say('4/4 app: building the game binary and the launcher')
    run(['make', f'-j{os.cpu_count() or 4}', 'runtime' if version == 'v0' else f'runtime-{version}', f'GAME={title}'], logs / 'runtime.log', cwd=tree, env=env)
    run(['xcrun', 'clang', '-fobjc-arc', '-O2', '-Wall', '-Ithird_party/d2r_gfx/gfx/nvn', '-Iruntime', *(['-DNSW_GAME_BOTW=1'] if title == 'botw' else [] if version == 'v0' else [f'-DTOTK_GAME_{version.upper()}=1']), 'app/launcher.m', '-framework', 'AppKit',
         '-o', 'build/app-launcher'], logs / 'launcher.log', cwd=tree, env=env)
    # used only while packaging (step_warm_metal); not part of the app
    run(['xcrun', 'clang', '-fobjc-arc', '-O2', '-Wall', '-Wextra', 'tools/warm_metal_cache.m', '-framework', 'Metal',
         '-framework', 'Foundation', '-o', 'build/warm-metal-cache'], logs / 'warm-metal-cache-build.log', cwd=tree, env=env)
    if not (tree / 'build/AppIcon.icns').exists():   # the game's own icon from its control data; optional
        try:
            run([tree / '.venv/bin/python', 'scripts/extract_app_icon.py', game, '--keys', keys, '--output', 'build/AppIcon.icns', *(['--title', '01007EF00011E000'] if title == 'botw' else [])],
                logs / 'app_icon.log', cwd=tree)
        except BuildError as e:
            say(f'   no app icon, the app gets the default one ({str(e).splitlines()[0]})')


def clone(src, dst):
    """APFS clone when on the same volume (no extra space), else a copy."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if subprocess.run(['cp', '-c', src, dst], capture_output=True).returncode:
        shutil.copy2(src, dst)


def copy_tree(src, dst):
    """One recursive APFS clone (95,646 seed files: 9 s, where one cp per file took 3.5 min), else a copy per file.
    -L copies what links point at, as the per-file copy did."""
    if not src.is_dir():   # an optional input this build does not have
        return
    dst.mkdir(parents=True, exist_ok=True)
    if subprocess.run(['cp', '-cRL', f'{src}/.', dst], capture_output=True).returncode:
        for p in sorted(src.rglob('*')):
            if p.is_file():
                clone(p, dst / p.relative_to(src))


def sign_app(app, logs):
    # Seal the bundle, including Info.plist and game resources. Copied helper
    # executables keep their existing signatures and byte identity.
    run(['codesign', '--force', '--sign', '-', app], logs / 'sign-app.log')
    # The seal was just hashed from these same files; re-reading ~16 GB of resources added ~12 s per build. The code
    # signatures are still verified, and the build runs the bundled executables afterwards.
    run(['codesign', '--verify', '--deep', '--strict', '--ignore-resources', app], logs / 'verify-app-signature.log')


def step_app(tree, out, logs, *, version="v0", title="totk", aoc=None):
    expected = BOTW_EXPECTED if title == 'botw' else VERSIONS[version]
    app_name = 'Breath of the Wild' if title == 'botw' else APP_NAME
    bundle_id = 'org.macnativegames.botw' if title == 'botw' else BUNDLE_ID
    chain = 'botw-native-v1-control-size' if title == 'botw' else 'totk-native-v4-unverified'
    minimum = 8000
    tmp = out.with_name(out.name + '.partial')
    if tmp.exists():
        shutil.rmtree(tmp)
    res = tmp / f'Contents/Resources/{title}'
    clone(tree / ('build/totk-native' if version == 'v0' else f'build/totk-native-{version}'), res/'build/totk-native')
    for rel in ('build/pso_manifest.tsv', 'build/pso_title.tsv', 'build/spirv-cross/spirv-cross',
                f'rewritten/{version}/main.patched.bin', f'rewritten/{version}/sdk.patched.bin',
                f'rewritten/{version}/subsdk0.patched.bin', f'extracted/{version}/romfs/romfs.bin', f'extracted/{version}/romfs/runtime-index.bin'):
        clone(tree / rel, res / rel)
    clone(tree / 'prebuilt/macos-arm64/totk_xlate', res / 'build/shader-service/totk_xlate')   # where totk_shader_rt.m looks for it
    for name in ('romfs.bin', 'runtime-index.bin') if aoc else ():   # BOTW add-on content, mounted as aoc: (romfs.c)
        clone(aoc / name, res / 'extracted/aoc' / name)
    for rel in (f'extracted/{version}/romfs-patches', f'extracted/{version}/romfs-patches-out', 'extracted/firmware/shared-fonts'):
        copy_tree(tree / rel, res / rel)
    seed = tree/f'build/shader-seed/{version}'
    if not (seed/'seed-manifest.json').is_file():
        raise BuildError('startup shader seed was not prepared; refusing an app without first-launch warm-up data')
    try:
        report = json.loads((seed/'seed-manifest.json').read_text())
        if (report['version'] != version or report['chain'] != chain or
                report['shaders'] < minimum or not report['files'] or any(
                    Path(name).name != name or sha256(seed/name) != digest for name, digest in report['files'].items())):
            raise ValueError('version, translation chain or file hash differs')
    except (ValueError, KeyError, OSError, TypeError) as error:
        raise BuildError(f'invalid prepared startup shader seed: {error}') from None
    copy_tree(seed, res/'build/shader-seed')
    for src in sorted(seed.glob('*.metal')):
        lib = tree/f'build/shader-libs/{sha256(src)}.metallib'
        if lib.is_file():
            clone(lib, res/'build/shader-libs'/lib.name)
    clone(tree / 'build/app-launcher', tmp / f'Contents/MacOS/{title.upper()}')
    info = dict(CFBundleDevelopmentRegion='en', CFBundleExecutable=title.upper(), CFBundleIdentifier=bundle_id, CFBundleName=app_name,
                CFBundleDisplayName=app_name, CFBundlePackageType='APPL', CFBundleShortVersionString=expected['display_version'],
                CFBundleVersion=str(expected['version'] + 1), LSMinimumSystemVersion='26.0', NSHighResolutionCapable=True,
                LSApplicationCategoryType='public.app-category.action-games', LSSupportsGameMode=True,
                GCSupportsControllerUserInteraction=True)
    icon = tree / 'build/AppIcon.icns'
    if icon.exists():
        clone(icon, tmp / 'Contents/Resources/AppIcon.icns')
        info['CFBundleIconFile'] = 'AppIcon'
    import plistlib
    (tmp / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    (tmp / 'Contents/PkgInfo').write_bytes(b'APPL????')
    if (tree / 'build/build-manifest.json').is_file():
        clone(tree / 'build/build-manifest.json', tmp / 'Contents/Resources/build-manifest.json')
    sign_app(tmp, logs)  # A failed seal must not remove the previous working app.
    old_binary = out/f'Contents/Resources/{title}/build/totk-native'
    notified = 0
    while old_binary.is_file():
        # Preparing/signing a replacement is safe while playing; replacing the
        # resources of an active game is not. Wait without closing the player.
        processes = subprocess.run(['ps', '-axo', 'comm='], capture_output=True, text=True, check=True).stdout.splitlines()
        if str(old_binary.resolve()) not in (p.strip() for p in processes):
            break
        if time.monotonic()-notified >= 60:
            say('4/4 app: replacement is ready; waiting for the running game to close before updating it')
            notified = time.monotonic()
        time.sleep(2)
    if out.exists():
        shutil.rmtree(out)
    tmp.rename(out)


def last_json(log):
    """The tool's report: its last line that is a JSON object."""
    for line in reversed(log.read_text(errors='replace').splitlines()):
        if line.startswith('{'):
            try: return json.loads(line)
            except ValueError: pass
    return None


def metal_cache_users():
    """Programs that have the Metal library cache of programs without an app bundle open, as 'name (pid)'. Whatever a
    program compiles there while another program that opened it first is running is lost when that other program quits
    (2026-10-18: 20 new libraries found while it ran, 2 after it quit; a packaging run with a short-lived probe running at
    its start kept 1 of 64)."""
    try:
        cache = subprocess.run(['getconf', 'DARWIN_USER_CACHE_DIR'], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return []
    files = sorted(str(f) for f in Path(cache, 'com.apple.metal').glob('*/libraries.*'))
    if not files:
        return []
    try:
        listed = subprocess.run(['lsof', '-F', 'pc', '--', *files], capture_output=True, text=True).stdout
    except OSError:
        return []
    users, pid = set(), None
    for line in listed.splitlines():
        if line.startswith('p'):
            pid = line[1:]
        elif line.startswith('c') and pid:
            users.add(f'{line[1:]} ({pid})')
    return sorted(users)


def step_warm_metal(tree, out, logs):
    """Compile the app's startup shaders once into this Mac's Metal cache (2026-10-18). The game compiles each of them from
    source when it first meets it, and Metal keeps the result keyed by the source text, the compile options and the length
    of the path of the compiling program's folder (only its length: another folder with a path as long finds them too);
    on a Mac that has never compiled them (a new Mac, after the system purged its cache, or after the app moved) the game's
    background warm-up spends about 3 minutes (M4, 8 threads) compiling them, and the world stutters if the player goes
    in before it is done. The tool (games/totk/tools/warm_metal_cache.m) compiles the same files with the same options from
    the game binary's own folder, and is removed again afterwards (the app's signature seals that folder). It runs only
    while no other program has Metal's cache open (what it compiled would be lost when that program quits), and the
    result is checked in a new process. Nothing here can make the build fail: without it the game compiles the shaders
    itself, as before."""
    build = out / 'Contents/Resources/totk/build'
    seed = build / 'shader-seed'
    tool = build / 'warm-metal-cache'   # next to the game binary: Metal matches the length of the program's folder path
    tool.unlink(missing_ok=True)        # left by an interrupted build
    sources = sorted(seed.glob('*_b.metal'))
    if sources and all((build / 'shader-libs' / f'{sha256(src)}.metallib').is_file() for src in sources):
        say('4/4 app: the startup shaders are compiled into the app (step_shader_libs), so Metal\'s cache needs no warming')
        return
    users = metal_cache_users()
    if users or subprocess.run(['pgrep', '-q', 'totk-native']).returncode == 0:
        who = ', '.join(users) if users else 'a game'
        say(f"4/4 app: {who} has Metal's shader cache open, so the startup shaders are compiled on the first start instead "
            '(what this build compiled now would be lost when that program quits)')
        return
    say("4/4 app: compiling the startup shaders for this Mac's GPU once (about 2 minutes on an M4; minutes saved on the first start)")
    t0 = time.time()
    try:
        clone(tree / 'build/warm-metal-cache', tool)
        run([tool, seed], logs / 'warm-metal-cache.log')
        run([tool, seed, '--verify', '64'], logs / 'warm-metal-cache-verify.log', quiet=False)
    except (BuildError, OSError) as e:
        say(f'   not done, the first start compiles them instead: {str(e).splitlines()[0]}')
        return
    finally:
        tool.unlink(missing_ok=True)
    done, sample = last_json(logs / 'warm-metal-cache.log'), last_json(logs / 'warm-metal-cache-verify.log')
    if not done or not sample:
        say(f'   no report from the tool, the first start compiles them instead (see {logs / "warm-metal-cache.log"})')
        return
    if done.get('failed'):
        say(f"   {done['failed']} shaders do not compile on this Mac (the game reports them in its log too); see {logs / 'warm-metal-cache.log'}")
    if sample['sampled'] and sample['hits'] * 10 >= sample['sampled'] * 9:
        say(f"   {done['compiled']} shaders compiled in {time.time() - t0:.0f} s; a new program finds them in Metal's cache "
            f"({sample['hits']} of {sample['sampled']} sampled, median {sample['median_ms']:.1f} ms). Moving the app makes them "
            f"cold again.")
    else:
        users = metal_cache_users()
        why = (f"{', '.join(users)} has Metal's cache open now" if users else
               'another program probably used Metal\'s cache meanwhile; build again with no other program running to retry')
        say(f"   compiled, but Metal did not keep them ({sample['hits']} of {sample['sampled']} sampled found, median "
            f"{sample['median_ms']:.0f} ms; {why}); the first start compiles them instead")


def parse_args(argv):
    ap = argparse.ArgumentParser(description='Build a native macOS app from your own BOTW 1.9.0 or supported TOTK game files; TOTK also supports iOS.',
                                 epilog='example: python3 build_app.py ~/Games/totk.xci -k ~/keys/prod.keys')
    ap.add_argument('game', type=Path, help='the game file (.xci or .nsp), base game 1.0.0')
    ap.add_argument('--game', dest='title', choices=('totk', 'botw'), default='totk', help='game to build; BOTW requires its 1.9.0 update')
    ap.add_argument('--dlc', type=Path, action='append', default=[], help='BOTW: an add-on content NSP; give both packs (the game enables its DLC only with both)')
    ap.add_argument('--reuse-data', type=Path, help='BOTW: reuse a previously verified runtime RomFS (checks the complete image and file table; executable is rebuilt)')
    ap.add_argument('--shaders-only', action='store_true', help='prepare and compile shaders into Documents/NSW Games without building or starting the game')
    ap.add_argument('--update', type=Path, help='separate supported cumulative update NSP/NSZ; outputs the same single app')
    ap.add_argument('--platform', choices=('macos','ios'), default='macos', help='build a macOS app (default) or unsigned iPhone/iPad IPA')
    ap.add_argument('-k', '--keys', type=Path, required=True, help='your prod.keys')
    ap.add_argument('-o', '--out', type=Path, help=f'the app or IPA to write (default depends on --platform; next to the game file)')
    ap.add_argument('--work', type=Path, help='work folder (default: BOTW_build or TOTK_build next to the game file)')
    ap.add_argument('--no-shader-reminder', action='store_true', help='iOS: do not install the Mac agent that asks, while an iPhone / iPad with the game is connected, to send the shaders the shared list lacks')
    fonts = ap.add_mutually_exclusive_group()
    fonts.add_argument('--firmware', type=Path, help='unpacked firmware NCA directory providing the six expected shared fonts (needed for NSP unless --shared-fonts or valid cached fonts are available)')
    fonts.add_argument('--shared-fonts', type=Path, help='previously extracted shared-font directory containing the six verified .bfttf files')
    return ap.parse_args(argv)


_WORK_LOCKS = []


def lock_work(work):
    """One build or build_shaders.py run per work folder, held until the process exits: two at once share the staging
    folders, receipts and seeds, and a second shader run once replaced files the first was still publishing (2026-10-21)."""
    work.mkdir(parents=True, exist_ok=True)
    handle = (work / '.build.lock').open('a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise BuildError(f'another build or build_shaders.py is using {work}; wait for it to finish') from None
    _WORK_LOCKS.append(handle)


@contextlib.contextmanager
def shader_cache_lock():
    games = Path(os.environ.get('NSW_GAMES_DIR', Path.home() / 'Documents/NSW Games'))
    slot = games / '.runtime/game-slot'
    slot.mkdir(parents=True, exist_ok=True)
    with (slot / 'lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BuildError('close the game before preparing its shader cache') from None
        running = subprocess.run(['ps', '-axo', 'comm='], capture_output=True, text=True, check=True).stdout.splitlines()
        if any(Path(name.strip()).name.startswith('totk-native') for name in running):
            raise BuildError('close the running native game before preparing shaders')
        yield


def install_shader_cache(tree, logs, *, version='v0', title='totk', seed=None):
    tool = tree / 'build/install-shader-cache'
    flags = ['-DNSW_GAME_BOTW=1'] if title == 'botw' else [] if version == 'v0' else [f'-DTOTK_GAME_{version.upper()}=1']
    run(['xcrun', 'clang', '-fobjc-arc', '-O2', '-Wall', '-Wextra', '-Werror',
         '-Iapp', '-Iruntime', '-Ithird_party/d2r_gfx/gfx/nvn', *flags,
         'tools/install_shader_cache.m', '-framework', 'Foundation', '-o', tool], logs / 'shader-cache-installer-build.log', cwd=tree)
    with shader_cache_lock():
        run([tool, seed or tree / f'build/shader-seed/{version}'], logs / 'shader-cache-install.log', cwd=tree)
    report = last_json(logs / 'shader-cache-install.log')
    if not report or not report.get('available'):
        raise BuildError('shader-cache installation did not produce a verified report')
    say(f"Shader cache ready: {report['cache']} ({report['shaders']} variants, {report['precompiled']} precompiled libraries)")
    return report


def main(argv):
    args = parse_args(argv)
    if args.shaders_only:
        if args.platform != 'macos':
            raise BuildError('--shaders-only prepares the macOS cache; use --platform macos')
        if not metal_compiler():
            raise BuildError('offline shader compilation needs Apple\'s Metal toolchain: xcodebuild -downloadComponent MetalToolchain')
    return build_selected(args)


def build_selected(args):
    if args.title == 'botw':
        from games.botw.build import build_botw
        return build_botw(args, sys.modules[__name__])
    if args.reuse_data:
        raise BuildError('--reuse-data currently applies to BOTW only')
    if args.dlc:
        raise BuildError('--dlc currently applies to BOTW only')
    game, keys = args.game.expanduser().resolve(), args.keys.expanduser().resolve()
    if not game.is_file():
        raise BuildError(f'no such file: {game}')
    if not keys.is_file():
        raise BuildError(f'no such file: {keys}')
    out = (args.out.expanduser().resolve() if args.out else game.parent / (f'{APP_NAME}.ipa' if args.platform=='ios' else f'{APP_NAME}.app'))
    work = (args.work.expanduser().resolve() if args.work else game.parent / 'TOTK_build')
    lock_work(work)
    if out.suffix.lower() != ('.ipa' if args.platform=='ios' else '.app'):
        raise BuildError(f'the {args.platform} output must end in {".ipa" if args.platform=="ios" else ".app"}: {out}')
    kind = input_kind(game)
    if kind not in ('xci', 'nsp'):
        raise BuildError('the base game must be an uncompressed XCI or NSP; NSZ is supported for update packages')
    update = args.update.expanduser().resolve() if args.update else None
    if update and (not update.is_file() or input_kind(update) not in ('nsp', 'nsz')):
        raise BuildError('--update must be a separate cumulative NSP or NSZ')
    version = 'v0'
    firmware = args.firmware.expanduser().resolve() if args.firmware else None
    shared_fonts = args.shared_fonts.expanduser().resolve() if args.shared_fonts else None
    if firmware and not firmware.is_dir():
        raise BuildError(f'no firmware directory: {firmware}')
    if shared_fonts:
        font_report(shared_fonts)
    if kind == 'nsp' and not args.shaders_only and not (firmware or shared_fonts or fonts_valid(work_tree(work, 'totk') / 'extracted/firmware/shared-fonts')):
        raise BuildError('NSP files do not include system shared fonts. Supply --firmware <unpacked firmware NCA directory> '
                         'or --shared-fonts <previously extracted font directory>; valid fonts in --work are also reused.')
    py = prerequisites(args)
    def existing_volume_path(path):
        path=path.resolve()
        while not path.exists(): path=path.parent
        return path
    work_volume=existing_volume_path(work.parent if work.parent.exists() else game.parent)
    output_volume=existing_volume_path(out.parent if out.parent.exists() else game.parent)
    free=shutil.disk_usage(work_volume).free/2**30
    output_free=shutil.disk_usage(output_volume).free/2**30
    same_volume=os.stat(work_volume).st_dev==os.stat(output_volume).st_dev
    base_cached=(work_tree(work, 'totk')/'extracted/v0/romfs/romfs.bin').exists()
    say(f'work folder {work} ({free:.0f} GB free on its volume)')
    if args.platform=='ios':
        if same_volume:
            required=(60 if update else 45) if not base_cached else (40 if update else 18)
        else:
            required=(40 if update else 25) if not base_cached else (25 if update else 18)
        if free<required:
            raise BuildError(f'iOS build needs about {required} GB free on the work/output volume for the IPA archive')
        if not same_volume and output_free<18:
            raise BuildError('the output volume needs about 18 GB free for the iOS IPA')
    else:
        if update and free<40 and not base_cached:
            raise BuildError('needs about 40 GB free for a fresh base + update build (19 GB extra with base data cached)')
        if free<25 and not base_cached:
            raise BuildError('needs about 25 GB free for the work folder (the app itself is an APFS clone of it when on the same volume)')
    logs = work / 'logs'
    env = dict(os.environ, PATH=os.pathsep.join(TOOL_PATHS + [os.environ.get('PATH', '/usr/bin:/bin:/usr/sbin:/sbin')]))
    t0 = time.time()
    tree = prepare_work(work, py, logs)
    if update:
        version, update = identify_update(tree, update, keys, logs)
    step_code(tree, game, keys, env, logs)
    if update:
        step_code(tree, update, keys, env, logs, version=version)
    if args.shaders_only:
        step_romfs(tree, keys, logs)
    else:
        step_data(tree, game, keys, logs, firmware=firmware, shared_fonts=shared_fonts)
    if update:
        step_update_data(tree, game, update, keys, logs, version=version)
    step_shaders(tree, logs, version=version)
    step_shader_libs(tree, logs, version=version, required=args.shaders_only)
    if args.platform == 'macos':
        install_shader_cache(tree, logs, version=version)
    if args.shaders_only:
        return
    step_binary(tree, game, keys, env, logs, version=version)
    say(f'4/4 app: assembling {out}')
    if args.platform=='ios':
        run([py, 'ios/tools/build_ipa.py', '--tree', tree, '--out', out, '--version', version,
             '--logs', logs, '--app-name', APP_NAME, '--bundle-id', BUNDLE_ID], logs/'build-ipa.log', cwd=tree, env=env)
        if not args.no_shader_reminder:
            # The iOS app never asks during play; while the device is connected, this Mac asks to send the shaders the
            # shared list lacks (ios/tools/shader_contrib_watch.py). Not essential: a failure only says so.
            try:
                run([py, 'ios/tools/shader_contrib_watch.py', 'install'], logs/'shader-contrib-watch.log', cwd=tree, env=env)
                say('   installed the Mac reminder for iPhone / iPad shader contributions (remove: python3 '
                    'games/totk/ios/tools/shader_contrib_watch.py uninstall)')
            except BuildError as e:
                say(f'   the Mac reminder for iPhone / iPad shader contributions was not installed: {e}')
    else:
        step_app(tree, out, logs, version=version)
    # Refresh an existing app's version, executable and signing identity before
    # the next Finder launch; the desktop alias keeps pointing to this bundle.
    if args.platform=='macos': run([LSREGISTER, '-f', out], logs / 'register-app.log')
    # macOS scans a new app the first time it runs (minutes for 16 GB, with nothing on screen): run it once here, in its
    # check-only mode, so the player's first start is immediate.
    if args.platform=='macos':
        say('4/4 app: macOS checks the new app once (a few minutes for the 16 GB bundle)')
        t1 = time.time()
        r = subprocess.run([out / 'Contents/MacOS/TOTK', '--check'], capture_output=True, text=True, timeout=1800)
        if r.returncode: raise BuildError(f'the app does not start: {(r.stdout + r.stderr).strip()}')
        say(f'   checked in {time.time() - t1:.0f} s')
        step_warm_metal(tree, out, logs)
    say(f'done in {(time.time() - t0) / 60:.0f} min: {out}')
    if args.platform=='macos':
        say('Open it from Finder. Moving it afterwards (also to /Applications) keeps the macOS check when it stays on the same '
            'volume, but Metal then compiles the startup shaders again in the background on the next start (a few minutes): '
            'pass -o with the final location instead. The first start verifies the game data and loads the prepared startup '
            'shaders. Additional scene/binding variants are translated and cached as they appear.')
    else: say('Install the unsigned IPA with a sideloading tool. The iOS build needs iOS/iPadOS 26 and a supported game controller; verify gameplay on your device.')


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except BuildError as e:
        print(f'\nERROR: {e}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
