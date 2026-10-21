#!/usr/bin/env python3
"""Prepare game shaders from the selected, verified local RomFS, without a played cache.

Every shader in the RomFS is found (Shader/ packages, SDK and boot packages, and the ones effects and UI layouts carry).
The startup shaders (Shader/, SDK, boot) are translated for the default bind state; then every bind state the shared
shader list (assets/shader_list.tsv, docs/shader-list.md) names for a shader of this game is translated too, so an
iPhone or iPad, which cannot translate while playing, carries what players have met. The output contains game-derived
data and belongs in the work folder, never in the repo.
"""
import argparse
import concurrent.futures
import hashlib
import inspect
import itertools
import json
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import sys
import threading
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shader_list import ShaderList   # noqa: E402

CHAIN = 'totk-native-v4-unverified'
MIN_SHADERS = 8000
ZSTD = b'\x28\xb5\x2f\xfd'
ANSWERS = dict(tex={}, img={}, attr={}, fout={})
LIST = Path(__file__).resolve().parents[1]/'assets/shader_list.tsv'


def suffix(answers):
    return hashlib.sha256(json.dumps(answers, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:12]


SUFFIX = suffix(ANSWERS)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked(data, offset, size):
    if offset < 0 or size < 0 or offset + size > len(data):
        raise ValueError('shader container range outside file')
    return data[offset:offset+size]


def sarc(data):
    if data[:4] != b'SARC':
        raise ValueError('expected SARC')
    endian = '<' if data[6:8] == b'\xff\xfe' else '>'
    header, size, start = struct.unpack_from(endian+'HxxII', data, 4)
    if size != len(data) or header < 20 or checked(data, header, 4) != b'SFAT':
        raise ValueError('invalid SARC header')
    count = struct.unpack_from(endian+'H', data, header+6)[0]
    names = header+12+count*16
    if checked(data, names, 4) != b'SFNT':
        raise ValueError('invalid SARC string table')
    names += 8
    result = {}
    for i in range(count):
        _, attribute, lo, hi = struct.unpack_from(endian+'4I', checked(data, header+12+i*16, 16))
        off = names+(attribute & 0xffffff)*4
        if attribute >> 24 != 1 or not names <= off < start or hi < lo or start+lo < start:
            raise ValueError('invalid SARC member')
        name = data[off:data.index(0, off, start)].decode('utf-8')
        if name.startswith('/') or '\\' in name or any(p in ('', '.', '..') for p in name.split('/')) or name in result:
            raise ValueError('unsafe or duplicate SARC name')
        result[name] = checked(data, start+lo, hi-lo)
    return result


def _short_control_size(control):
    if len(control) < 36:
        return None
    h = struct.unpack_from('<9I', control)
    if h[:2] != (0x98761234, 1) or h[3:5] != (0x120, 11) or h[7] or h[8] != len(control):
        return None
    # Format 9 appends reflection after its 0x7d1-byte control; reflection can contain relocated pointers.
    if h[2] == 9 and h[5] == 0x7d0 and h[8] == 0x7d1 + h[6]:
        return 0x7d1
    if h[2] == 14 and not h[5] and not h[6] and h[8] == 0x848:
        return 0x848
    return None


def bnsh_shaders(data, *, short_control=False):
    """BNSH absolute offsets are relative to each embedded BNSH, not BFSHA."""
    if data[:4] != b'BNSH' or checked(data, 12, 2) != b'\xff\xfe' or checked(data, 96, 4) != b'grsc':
        raise ValueError('unsupported BNSH header')
    size = struct.unpack_from('<I', data, 28)[0]
    data = checked(data, 0, size)
    count, variations = struct.unpack_from('<IQ', data, 124)
    checked(data, variations, count*64)
    for i in range(count):
        program = struct.unpack_from('<Q', data, variations+i*64+16)[0]
        checked(data, program, 56)
        for stage, relative in [('vs', 8), ('ps', 40), ('cs', 48)]:
            offset = struct.unpack_from('<Q', data, program+relative)[0]
            if not offset:
                continue
            control_off, code_off, code_size, control_size = struct.unpack_from('<QQII', checked(data, offset+8, 24))
            control = checked(data, control_off, control_size)
            code = checked(data, code_off, code_size)
            if short_control:
                canonical_size = _short_control_size(control)
                if canonical_size is None:
                    raise ValueError('unsupported short NVN shader control')
                control = control[:canonical_size]
            elif len(control) < 0x880:
                continue
            else:
                control = control[:0x880]   # some are 0x88c; TOTK and its runtime key read 0x880
            c1, length = struct.unpack_from('<II', control, 0x700)
            actual = struct.unpack_from('<I', control, 0x714)[0]
            if actual != {'vs': 0, 'ps': 1, 'cs': 5}[stage] or not 0 <= c1 <= length <= len(code) or not length:
                raise ValueError('invalid NVN shader bounds or stage')
            code = code[:length]
            yield digest(control+code), stage, control, code


def sharcb_shaders(data, *, short_control=False):
    """agl shader packages (.sharcb, magic BAHS): the records between the header's start and the code pool are
    <u64 code offset in the pool, u32 control size, i32 offset of the control from the record>; a record may reuse an
    earlier record's control. TOTK hashes its first 0x880 control bytes; BOTW omits appended reflection."""
    if len(data) < 32 or data[:4] != b'BAHS':
        raise ValueError('expected an agl shader package')
    start, pool_size, pool = struct.unpack_from('<3I', data, 20)
    if not 32 <= start <= pool or pool + pool_size > len(data):
        raise ValueError('agl shader package layout outside the file')
    seen = set()
    sizes = (0x880, 0x88c)
    if short_control:
        sizes, at = set(), start
        while (at := data.find(b'\x34\x12\x76\x98', at, pool)) >= 0:
            if at + 36 <= pool:
                size = struct.unpack_from('<I', data, at + 32)[0]
                if 36 <= size <= pool - at and _short_control_size(data[at:at+size]) is not None:
                    sizes.add(size)
            at += 4
    for control_size in sorted(sizes):
        size = struct.pack('<I', control_size)
        at = start
        while (at := data.find(size, at, pool)) >= 0:
            record, at = at - 8, at + 4
            if record < start or record % 4:
                continue
            code_off = struct.unpack_from('<Q', data, record)[0]
            control_at = record + struct.unpack_from('<i', data, record+12)[0]
            length = control_size if short_control else min(control_size, 0x880)
            if not start <= control_at <= pool - length or code_off >= pool_size:
                continue
            control = data[control_at:control_at+length]
            if short_control:
                canonical_size = _short_control_size(control)
                if canonical_size is None:
                    continue
                control = control[:canonical_size]
            c1, length = struct.unpack_from('<II', control, 0x700)
            stage = {0: 'vs', 1: 'ps', 5: 'cs'}.get(struct.unpack_from('<I', control, 0x714)[0])
            if not stage or not 0 < length <= pool_size - code_off or c1 > length:
                continue
            code = data[pool+code_off:pool+code_off+length]
            key = digest(control+code)
            if key not in seen:
                seen.add(key)
                yield key, stage, control, code


def startup_package(name):
    """The packages the startup shaders come from; effects and UI layouts carry more."""
    return (name == 'Lib/NintendoSDK/TextureShader.bnsh' or name == 'Pack/Bootup.Nin_NX_NVN.pack.zs' or
            name.startswith('Shader/') and name.endswith('.zs'))


def inventory(romfs, modules=None):
    """Every shader in the RomFS and the game modules -> (stage, control, code, source path), the keys translated for
    the default bind state (startup packages, agl packages, the modules), the archives. modules: rewritten/<version>."""
    import compression.zstd as zstd
    index = json.loads((romfs/'index.json').read_text())
    if index.get('status') != 'pass':
        raise ValueError('RomFS has not passed integrity verification')
    entries = {r['path']: r for r in index['files']}
    shaders, startup, archives = {}, set(), []
    with (romfs/'romfs.bin').open('rb') as stream:
        def read(name):
            row = entries[name]
            stream.seek(row['offset'])
            raw = stream.read(row['size'])
            if len(raw) != row['size']:
                raise ValueError('short RomFS shader read')
            return raw
        dictionaries = {}
        for raw in sarc(zstd.decompress(read('Pack/ZsDic.pack.zs'))).values():
            d = zstd.ZstdDict(raw)
            dictionaries[d.dict_id] = d
        def decode(raw):
            info = zstd.get_frame_info(raw)
            if info.decompressed_size is None or info.decompressed_size > 512 << 20:
                raise ValueError('unbounded shader package')
            return zstd.decompress(raw, zstd_dict=dictionaries[info.dictionary_id] if info.dictionary_id else None)
        def visit(name, data, keys, depth=0):
            if data[:4] == b'BAHS':   # agl: the engine's own shaders (title screen, post effects, lighting)
                for key, stage, control, code in sharcb_shaders(data):
                    shaders.setdefault(key, (stage, control, code, name))
                    keys.add(key); startup.add(key)
                return
            if data[:4] == b'SARC':
                if depth > 4:
                    return
                for member, raw in sarc(data).items():
                    visit(name+'/'+member, decode(raw) if raw[:4] == ZSTD else raw, keys, depth+1)
                return
            # BFSHA and BNSH files, and effect / layout resources, hold complete, independently addressed BNSH files.
            pos = 0
            while True:
                pos = data.find(b'BNSH\0\0\0\0', pos)
                if pos < 0:
                    break
                size = struct.unpack_from('<I', checked(data, pos+28, 4))[0]
                for key, stage, control, code in bnsh_shaders(checked(data, pos, size)):
                    shaders.setdefault(key, (stage, control, code, name))
                    keys.add(key)
                pos += max(size, 8)
        for name in sorted(entries):
            raw = read(name)
            # Only containers can hold shaders: compressed files, packages and shader archives.
            if raw[:4] not in (ZSTD, b'SARC', b'FSHA', b'BNSH', b'BAHS') and b'BNSH\0\0\0\0' not in raw:
                continue
            data = decode(raw) if raw[:4] == ZSTD else raw
            keys = set()
            visit(name, data, keys)
            if startup_package(name):
                startup |= keys
            if keys or startup_package(name):
                archives.append(dict(path=name, sha256=digest(raw)))
        for module in sorted(Path(modules).glob('*.patched.bin')) if modules else []:
            keys = set()   # the game module embeds a few BNSH files
            visit('module:'+module.name[:-len('.patched.bin')], module.read_bytes(), keys)
            startup |= keys
        print(f'discovered {len(shaders)} unique shaders, {len(startup)} of them translated for the default bind state',
              flush=True)
    if not startup:
        raise ValueError('no startup shaders discovered')
    return index, shaders, startup, archives


def located(locators, read):
    """Shaders the shared list locates outside BNSH files (agl .sharcb, the game module), read where it says and kept
    only when control and code hash to the listed key (another game version's bytes never are). read(source) -> bytes."""
    found = {}
    for key, source, control_at, code_at, length in sorted(locators):
        data = read(source)
        if data is None or not 0 < length <= 16 << 20:
            continue
        control, code = data[control_at:control_at+0x880], data[code_at:code_at+length]
        if len(control) != 0x880 or len(code) != length or digest(control+code) != key:
            continue
        stage = {0: 'vs', 1: 'ps', 5: 'cs'}.get(struct.unpack_from('<I', control, 0x714)[0])
        c1, size = struct.unpack_from('<II', control, 0x700)
        if stage and size == length and 0 <= c1 <= length:
            found[key] = (stage, control, code, source)
    return found


def source_reader(romfs, modules):
    """read(source) for locator rows: a RomFS path, each '/' after a package entering it (.zs decompressed), or
    module:<name>, a game module's image (rewritten/<version>/<name>.patched.bin). None when it is not there."""
    import compression.zstd as zstd
    index = json.loads((romfs/'index.json').read_text())
    entries = {r['path']: r for r in index['files']}
    stream = (romfs/'romfs.bin').open('rb')
    def raw(name):
        row = entries[name]; stream.seek(row['offset']); return stream.read(row['size'])
    dictionaries = {}
    for data in sarc(zstd.decompress(raw('Pack/ZsDic.pack.zs'))).values():
        d = zstd.ZstdDict(data); dictionaries[d.dict_id] = d
    def decode(data):
        if data[:4] != ZSTD:
            return data
        info = zstd.get_frame_info(data)
        return zstd.decompress(data, zstd_dict=dictionaries[info.dictionary_id] if info.dictionary_id else None)
    cache = {}
    def read(source):
        if source in cache:
            return cache[source]
        data = None
        if source.startswith('module:'):
            path = Path(modules)/f'{source[7:]}.patched.bin' if modules else None
            data = path.read_bytes() if path and path.is_file() else None
        else:
            parts = source.split('/')
            for cut in range(len(parts), 0, -1):
                if '/'.join(parts[:cut]) in entries:
                    data = decode(raw('/'.join(parts[:cut])))
                    for member in parts[cut:]:
                        members = sarc(data) if data[:4] == b'SARC' else {}
                        if member not in members:
                            data = None; break
                        data = decode(members[member])
                    break
        cache[source] = data
        return data
    return read


def plan(shaders, startup, listed):
    """Translation jobs (key, answers, variant, why): the default bind state for every startup shader, then each bind
    state the shared list names for a shader found in this game ('listed'). Listed states the default already produced
    are dropped after translation (prepare), since only the translator knows a shader's deps."""
    jobs = [(key, ANSWERS, key+'-'+SUFFIX, 'default') for key in sorted(startup)]
    for key, deps in sorted(listed):
        if key in shaders:
            answers = {**ANSWERS, **json.loads(deps)}
            jobs.append((key, answers, key+'-'+suffix(answers), 'listed'))
    return jobs


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json(path, value, sync_directory=True):
    """Durable data; the directory entry is synced only where nothing else checks it. Receipts and staging indexes
    skip that (a directory fsync each in folders of ~300,000 entries cost minutes per run): the next run verifies them
    by hash, and publication syncs the staging folder once with the manifest."""
    temporary = path.with_name(path.name+'.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, sort_keys=True, separators=(',', ':'))
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)
    if sync_directory:
        _sync_directory(path.parent)


def prepare(romfs, output, translator, spirv_cross, version, shader_list=LIST, modules=None, *,
            inventory_fn=inventory, minimum_shaders=MIN_SHADERS, chain=CHAIN, variant_planner=None, extra_identity=None):
    """Resume verified requests; variant_planner(key, stage, default_reflection) lazily adds answer dictionaries."""
    shared = ShaderList.load(shader_list)
    listed = shared.variants
    identity = dict(schema=1, chain=chain, version=version,
                    source_romfs_sha256=json.loads((romfs/'index.json').read_text())['romfs_sha256'],
                    translator_sha256=digest(translator.read_bytes()),
                    spirv_cross_sha256=digest(spirv_cross.read_bytes()),
                    preparation_sha256=digest(Path(__file__).read_bytes()),
                    inventory_sha256=digest(Path(inspect.getsourcefile(inventory_fn)).read_bytes()),
                    variant_planner_sha256=digest(Path(inspect.getsourcefile(variant_planner)).read_bytes()) if variant_planner else None,
                    **({'variant_planner': variant_planner.__name__} if variant_planner else {}),   # one file, two coverage modes
                    shader_list_sha256=digest(Path(shader_list).read_bytes()) if Path(shader_list).is_file() else None)
    identity.update(extra_identity or {})   # inputs the inventory reads besides the RomFS (BOTW: the DLC image)
    previous = output.with_name(output.name+'.previous')
    if previous.exists() and not output.exists():
        previous.rename(output)   # Recover a kill between the two publication renames.
    marker = output/'seed-manifest.json'
    old = {}
    if marker.exists():
        try:
            old = json.loads(marker.read_text())
            if all(old.get(k) == v for k, v in identity.items()) and old.get('shaders', 0) >= minimum_shaders and all(
                    Path(name).name == name and digest((output/name).read_bytes()) == sha for name, sha in old['files'].items()):
                print(f'reusing {old["shaders"]} verified prepared startup shaders', flush=True)
                return old
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            old = {}
    producer = {name: identity[name] for name in ('chain', 'version', 'translator_sha256', 'spirv_cross_sha256')}
    legacy = old if all(old.get(name) == identity[name] for name in (*producer, 'source_romfs_sha256')) else {}
    staging = output.with_name(output.name+'.partial')
    staging.mkdir(parents=True, exist_ok=True)
    checkpoints = output.with_name(output.name+'.checkpoints')
    checkpoints.mkdir(parents=True, exist_ok=True)
    _, shaders, startup, archives = inventory_fn(romfs, modules)
    locators = {where for where in shared.locators if where[0] not in shaders}
    extra = located(locators, source_reader(romfs, modules)) if locators else {}
    shaders.update(extra)
    print(f'{len(extra)} more shaders read where the shared list locates them (agl .sharcb, game module)', flush=True)
    jobs = plan(shaders, startup, listed)
    # Receipts commit individual requests; expanding a plan never invalidates an already verified translation.
    by_key = {}
    for job in jobs:
        by_key.setdefault(job[0], []).append(job)
    outcome, produced = {}, {}
    lock = threading.Lock()
    pending = iter(by_key.items())
    done = [0, 0]   # completed, failed
    stop = threading.Event()
    processes = set()
    resumed = [0]
    asked = set()   # every variant this run requested: the receipts worth keeping
    started = time.monotonic()

    def cancel():
        stop.set()
        with lock:
            active = list(processes)
        for process in active:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for process in active:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)   # A compiler descendant can outlive its service parent.
            except ProcessLookupError:
                pass

    def semantic_deps(deps, stage, clip):
        return json.dumps([deps, clip if stage == 'vs' else None], sort_keys=True, separators=(',', ':'))

    def entry_hash(entry):
        return digest(json.dumps(entry, sort_keys=True, separators=(',', ':')).encode())

    def reflection_at(folder, variant, stage):
        reflection = json.loads((folder/(variant+'_b.json')).read_text())
        if reflection['chain'] != chain or reflection['stage'] != stage or not isinstance(reflection['deps'], dict):
            raise ValueError('prepared shader identity mismatch')
        return reflection

    def commit_variant(variant, fingerprint, entry):
        names = [entry['variant']+'_b.metal', entry['variant']+'_b.json']
        for name in names:
            with (staging/name).open('rb') as stream:
                if not stream.read(1):
                    raise ValueError('empty prepared shader payload')
                os.fsync(stream.fileno())
        _write_json(checkpoints/(variant+'.json'), dict(request_sha256=fingerprint, entry=entry,
                    index_row_sha256=entry_hash(entry), files={name: digest((staging/name).read_bytes()) for name in names}), sync_directory=False)

    def restore_files(names, folder):
        # A hard link: published files are only ever replaced by rename, never rewritten, so the published seed and the
        # next one can share them (BOTW's complete seed: ~294,000 copies took minutes on every identity change).
        # One link straight to the final name: the name appears pointing at a complete file, and each extra rename or
        # unlink in the 300,000-entry folder was most of a resume (sampled, 2026-10-21).
        if folder != staging:
            for name in names:
                try:
                    try:
                        os.link(folder/name, staging/name)
                    except FileExistsError:   # left by an interrupted run, unverified: replace it
                        (staging/name).unlink()
                        os.link(folder/name, staging/name)
                except OSError:   # another volume or no hard links: a durable copy, as before
                    temporary = staging/(name+'.restore')
                    shutil.copyfile(folder/name, temporary)
                    with temporary.open('rb') as stream:
                        os.fsync(stream.fileno())
                    temporary.replace(staging/name)
            # No directory fsync per restore (147,000 of a 300,000-entry folder took minutes): publication writes the
            # manifest with _write_json, whose directory fsync makes every entry durable, and a crash before that only
            # leaves a staging folder the next run checks file by file against the receipts.

    def resumed_variant(key, variant, fingerprint, stage, clip):
        names = {variant+'_b.metal', variant+'_b.json'}
        try:
            receipt = json.loads((checkpoints/(variant+'.json')).read_text())
            entry = receipt['entry']
            canonical = entry['variant']
            cached_names = {canonical+'_b.metal', canonical+'_b.json'}
            if (receipt['request_sha256'] == fingerprint and receipt['index_row_sha256'] == entry_hash(entry) and
                    canonical.startswith(key+'-') and all(Path(name).name == name for name in cached_names) and
                    set(receipt['files']) == cached_names):
                for folder in (staging, output):
                    if not all((folder/name).is_file() and digest((folder/name).read_bytes()) == sha for name, sha in receipt['files'].items()):
                        continue
                    reflection = reflection_at(folder, canonical, stage)
                    if entry == dict(variant=canonical, deps=reflection['deps'], **({'depth_minus_w': clip} if stage == 'vs' else {})):
                        restore_files(cached_names, folder)
                        return reflection, entry
        except (OSError, ValueError, KeyError, TypeError):
            pass
        # Older complete seeds have no receipts. Their hash-verified pair and index bootstrap the same request.
        try:
            files = legacy['files']
            if any(digest((output/name).read_bytes()) != files[name] for name in names | {key+'.variants'}):
                return None
            entries = json.loads((output/(key+'.variants')).read_text())
            reflection = reflection_at(output, variant, stage)
            entry = dict(variant=variant, deps=reflection['deps'], **({'depth_minus_w': clip} if stage == 'vs' else {}))
            if entry not in entries:
                return None
            restore_files(names, output)
            commit_variant(variant, fingerprint, entry)
            return reflection, entry
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def worker():
        process = None
        # A service orphaned by SIGKILL can only finish in its private directory, never overwrite resumed payloads.
        scratch = tempfile.TemporaryDirectory(prefix='translation-', dir=checkpoints)
        work = Path(scratch.name)
        try:
            while not stop.is_set():
                with lock:
                    item = next(pending, None)
                if item is None:
                    return
                key, group = item
                stage, control, code, source = shaders[key]
                shader_identity = dict(producer, stage=stage, control_sha256=digest(control), code_sha256=digest(code))
                mine, results, requested = {}, [], set()
                default_reflection = None
                def expanded_jobs():
                    if variant_planner and default_reflection is not None:
                        for more in variant_planner(key, stage, default_reflection):
                            more = {**ANSWERS, **more}
                            yield key, more, key+'-'+suffix(more), 'expanded'
                for _, answers, variant, why in itertools.chain(group, expanded_jobs()):
                    if stop.is_set():
                        return
                    clip = bool(answers.get('depth_minus_w', True))
                    dependencies = {name: value for name, value in answers.items() if name != 'depth_minus_w'}
                    answer_id = semantic_deps(dependencies, stage, clip)
                    if answer_id in requested or answer_id in mine:
                        continue   # the default (or an earlier row) already gave exactly these deps
                    requested.add(answer_id)
                    with lock:
                        asked.add(variant)
                    fingerprint = digest(json.dumps(dict(shader_identity, answers=dependencies, depth_minus_w=clip),
                                                    sort_keys=True, separators=(',', ':')).encode())
                    cached = resumed_variant(key, variant, fingerprint, stage, clip)
                    if cached:
                        refl, entry = cached
                        with lock:
                            resumed[0] += 1
                    else:
                        if process is None:
                            process = subprocess.Popen([str(translator), '--service', '--spirv-cross', str(spirv_cross),
                                                        '--out', str(work), '--chain', chain],
                                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, start_new_session=True)
                            with lock:
                                processes.add(process)
                            if stop.is_set():
                                cancel(); return
                        for name in (variant+'_b.metal', variant+'_b.json'):
                            (work/name).unlink(missing_ok=True)
                    c1, length = struct.unpack_from('<II', control, 0x700)
                    code_path = work/(variant+'.code')
                    request = dict(variant=variant, stage=stage, code=str(code_path.resolve()),
                                   c1=code[c1:length].hex(), **answers)
                    request.setdefault('depth_minus_w', True)
                    if stage == 'cs':
                        request['local'] = list(struct.unpack_from('<III', control, 0x748))
                    if cached:
                        reply = {'ok': True}
                    else:
                        code_path.write_bytes(code[0 if stage == 'cs' else 0x30:length])
                        process.stdin.write(json.dumps(request)+'\n'); process.stdin.flush()
                        response = process.stdout.readline()
                        if not response:
                            raise ValueError('shader preparation service exited unexpectedly')
                        reply = json.loads(response)
                        for scratch_file in work.glob(variant+'.*'):   # .code, .spv, .res.json: ~25 KB a request until exit otherwise
                            scratch_file.unlink()
                    if reply.get('ok'):
                        if not cached:
                            refl = reflection_at(work, variant, stage)
                        if why == 'default':
                            default_reflection = refl
                        deps = semantic_deps(refl['deps'], stage, clip)
                        if deps in mine:   # same deps as a variant already made: keep one
                            if not cached:
                                commit_variant(variant, fingerprint, mine[deps])
                                for name in (variant+'_b.metal', variant+'_b.json'):
                                    (work/name).unlink(missing_ok=True)
                            continue
                        if not cached:
                            entry = dict(variant=variant, deps=refl['deps'], **({'depth_minus_w': clip} if stage == 'vs' else {}))
                            for name in (variant+'_b.metal', variant+'_b.json'):
                                (work/name).replace(staging/name)
                            commit_variant(variant, fingerprint, entry)
                        mine[deps] = entry
                        emitted = entry['variant']
                        rows = [f'S\t{emitted}_b\t'+str({'vs': 0, 'ps': 1, 'cs': 5}[stage])]
                        if stage == 'cs':
                            rows.append(f'C\t{emitted}_b')
                        results.append(dict(status='ok', variant=emitted, rows=rows, why=why))
                    else:
                        results.append(dict(status='failed', key=key, variant=variant, why=why, stage=stage, source=source,
                                            error=reply.get('error')))
                    with lock:
                        done[0 if reply.get('ok') else 1] += 1
                        if sum(done) % 100 == 0:
                            print(f'prepared {done[0]} shader variants, skipped {done[1]}; {time.monotonic()-started:.1f}s', flush=True)
                entries = list(mine.values())
                if entries:
                    _write_json(staging/(key+'.variants'), entries, sync_directory=False)
                with lock:
                    outcome[key] = results
                    if entries:
                        produced[key] = entries
        except BaseException:
            cancel()
            raise
        finally:
            if process is not None:
                try:
                    process.stdin.close()
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                process.stdout.close()
                with lock:
                    processes.discard(process)
            scratch.cleanup()

    # At most 8 services, as compile_seed.py compiles: each translator holds its own working memory.
    workers = max(1, min(8, os.cpu_count() or 4, len(by_key)))
    pool = concurrent.futures.ThreadPoolExecutor(workers)
    handlers = {}
    def interrupted(number, _frame):
        if not stop.is_set():
            stop.set()
            raise KeyboardInterrupt(f'shader preparation interrupted by signal {number}')
    try:
        if threading.current_thread() is threading.main_thread():
            for number in (signal.SIGINT, signal.SIGTERM):
                handlers[number] = signal.signal(number, interrupted)
        for future in concurrent.futures.as_completed([pool.submit(worker) for _ in range(workers)]):
            future.result()
    except BaseException:
        cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        for number, handler in handlers.items():
            signal.signal(number, handler)
    failed, rows, completed = [], [], []
    results = [result for key in by_key for result in outcome.get(key, [])]
    for result in results:
        if result['status'] == 'ok':
            completed.append(result['variant']); rows.extend(result['rows'])
        else:
            failed.append(result)
    ready = sum(1 for key in startup if key in produced)
    if ready < minimum_shaders:
        raise ValueError(f'only {ready} shaders prepared; at least {minimum_shaders} are required for first-launch warm-up')
    # Only ship completed runtime inputs. Failed translations can leave MSL
    # behind before publishing reflection; those must not become cached shaders.
    wanted = set()
    for variant in completed:
        wanted.update([variant+'_b.metal', variant+'_b.json', variant.split('-')[0]+'.variants'])
    for p in staging.iterdir():
        if p.name not in wanted:
            p.unlink()
    with (staging/'pso_manifest.tsv').open('w') as stream:
        stream.write('# Prepared from this app\'s local game data; '+chain+'\n'+'\n'.join(rows)+'\n')
        stream.flush(); os.fsync(stream.fileno())
    report = dict(**identity, shaders=len(completed), discovered=len(shaders), located=len(extra), startup_shaders=ready,
                  listed_variants=sum(1 for _, _, _, why in jobs if why == 'listed'), skipped=failed,
                  expanded_variants=sum(result['why'] == 'expanded' for result in results), resumed_variants=resumed[0],
                  archives=archives, seconds=time.monotonic()-started,
                  files={p.name: digest(p.read_bytes()) for p in sorted(staging.iterdir())})
    _write_json(staging/'seed-manifest.json', report)
    if previous.exists():
        shutil.rmtree(previous)
    try:
        if output.exists():
            output.rename(previous)
        staging.rename(output)
        _sync_directory(output.parent)
    except BaseException:
        if previous.exists() and not output.exists():
            previous.rename(output)
        raise
    if previous.exists():
        shutil.rmtree(previous)
    # Receipts of variants this plan no longer requests only grow the folder (BOTW: 147,022 receipts, 574 MB, for a
    # bounded plan of ~26,000); a variant requested again later is translated again. Pruned only after publication.
    pruned = 0
    for receipt in checkpoints.glob('*.json'):
        if receipt.stem not in asked:
            receipt.unlink(missing_ok=True); pruned += 1
    if pruned:
        print(f'{pruned} receipts of variants this plan no longer requests removed', flush=True)
    print(f'startup seed ready: {len(completed)} shader variants ({ready} startup shaders, the rest from the shared list), '
          f'{len(failed)} skipped', flush=True)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ['romfs', 'output', 'translator', 'spirv-cross']:
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--version', required=True)
    p.add_argument('--shader-list', type=Path, default=LIST, help='the shared shader list (default: assets/shader_list.tsv)')
    p.add_argument('--modules', type=Path, help='rewritten/<version>: the game module images the list locates shaders in')
    a = p.parse_args()
    prepare(a.romfs.resolve(), a.output.resolve(), a.translator.resolve(), a.spirv_cross.resolve(), a.version,
            a.shader_list.resolve(), a.modules.resolve() if a.modules else None)


if __name__ == '__main__':
    main()
