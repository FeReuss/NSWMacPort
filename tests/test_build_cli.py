"""The offline path must install a cache without compiling or launching the game."""
from pathlib import Path
import os
import shutil
import json
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import build_app as build
from games.botw.build import build_botw


class BuildCLI(unittest.TestCase):
    def shader_fixture(self, tree):
        seed = tree / 'build/shader-seed/v0'
        seed.mkdir(parents=True)
        (seed / 'a.metal').write_text('// synthetic source a')
        (seed / 'seed-manifest.json').write_text(json.dumps({'files': {}}))
        compiler = tree / 'fake-metal'
        compiler.write_text(f'#!{sys.executable}\n' + r'''
import hashlib, os, struct, sys, time
from pathlib import Path
source = Path(sys.argv[-1])
output = Path(sys.argv[sys.argv.index('-o') + 1])
with Path(os.environ['TEST_METAL_CALLS']).open('a') as calls:
    calls.write(source.name + '\n')
if os.environ.get('TEST_METAL_FAIL'):
    sys.exit(1)
data = bytearray(88) + hashlib.sha256(source.read_bytes()).digest()
data[:4] = b'MTLB'
struct.pack_into('<Q', data, 16, len(data))
output.write_bytes(data[:12])
if os.environ.get('TEST_METAL_TRUNCATE'):
    sys.exit(0)
block = Path(os.environ['TEST_METAL_BLOCK'])
if source.name == 'b.metal' and block.exists():
    Path(os.environ['TEST_METAL_READY']).write_text(str(os.getpid()))
    while block.exists():
        time.sleep(0.02)
output.write_bytes(data)
''')
        compiler.chmod(0o755)
        env = dict(os.environ, TEST_METAL_CALLS=str(tree / 'calls'), TEST_METAL_BLOCK=str(tree / 'block'),
                   TEST_METAL_READY=str(tree / 'ready'))
        return seed, compiler, env

    def test_precompiled_libraries_are_not_recopied_when_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            tree = Path(folder)
            seed, compiler, env = self.shader_fixture(tree)
            with patch.object(build, 'metal_compiler', return_value=(str(compiler), 'test', env)):
                build.step_shader_libs(tree, tree / 'logs', required=True)
                target = seed / 'a.metallib'
                before = target.stat()
                with patch.object(build.subprocess, 'Popen', side_effect=AssertionError('unchanged run must not compile')):
                    build.step_shader_libs(tree, tree / 'logs', required=True)
                self.assertEqual(target.stat().st_ino, before.st_ino)
                self.assertEqual(target.stat().st_mtime_ns, before.st_mtime_ns)
                library = next((tree / 'build/shader-libs').glob('*.metallib'))
                self.assertEqual(target.stat().st_ino, library.stat().st_ino)   # published as a link, not a copy
            self.assertEqual((tree / 'calls').read_text().splitlines(), ['a.metal'])
            self.assertEqual(json.loads((seed / 'seed-manifest.json').read_text())['precompiled'], 1)

    def test_no_toolchain_keeps_verified_units_and_quarantines_unverified_ones(self):
        with tempfile.TemporaryDirectory() as folder:
            tree = Path(folder)
            seed, compiler, env = self.shader_fixture(tree)
            (seed / 'b.metal').write_text('// synthetic source b')
            with patch.object(build, 'metal_compiler', return_value=(str(compiler), 'test', env)):
                build.step_shader_libs(tree, tree / 'logs', required=True)
            first = tree / f'build/shader-libs/{build.sha256(seed / "a.metal")}.metallib'
            second = tree / f'build/shader-libs/{build.sha256(seed / "b.metal")}.metallib'
            second.with_suffix('.json').unlink()
            original = second.read_bytes()
            first_stat = first.stat()
            with patch.object(build, 'metal_compiler', return_value=None), \
                    patch.object(build.subprocess, 'Popen', side_effect=AssertionError('no compiler is available')):
                build.step_shader_libs(tree, tree / 'logs')
            self.assertEqual(first.stat().st_ino, first_stat.st_ino)
            self.assertFalse(second.exists())
            self.assertFalse((seed / 'b.metallib').exists())
            self.assertTrue(any(path.read_bytes() == original for path in second.parent.glob('*.previous')))
            report = json.loads((seed / 'seed-manifest.json').read_text())
            self.assertEqual(report['precompiled'], 1)
            self.assertNotIn('b.metallib', report['files'])
            with patch.object(build, 'metal_compiler', return_value=(str(compiler), 'test', env)):
                build.step_shader_libs(tree, tree / 'logs', required=True)
            calls = (tree / 'calls').read_text().splitlines()
            self.assertEqual(calls.count('a.metal'), 1)
            self.assertEqual(calls.count('b.metal'), 2)

    def test_corrupt_library_and_changed_flags_are_not_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            tree = Path(folder)
            seed, compiler, env = self.shader_fixture(tree)
            libs = tree / 'build/shader-libs'
            with patch.object(build, 'metal_compiler', return_value=(str(compiler), 'test', env)):
                build.step_shader_libs(tree, tree / 'logs', required=True)
                library = libs / f'{build.sha256(seed / "a.metal")}.metallib'
                original = library.read_bytes()
                library.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
                build.step_shader_libs(tree, tree / 'logs', required=True)
                self.assertEqual(library.read_bytes(), original)
                with patch.object(build, 'METAL_FLAGS', [*build.METAL_FLAGS, '-different-test-flag']):
                    env['TEST_METAL_FAIL'] = '1'
                    with self.assertRaisesRegex(build.BuildError, 'no shader libraries'):
                        build.step_shader_libs(tree, tree / 'logs', required=True)
                    self.assertFalse(library.exists())
                    self.assertFalse((seed / 'a.metallib').exists())
                    self.assertTrue(any(p.read_bytes() == original for p in libs.glob('*.previous')))
                    self.assertNotIn('a.metallib', json.loads((seed / 'seed-manifest.json').read_text())['files'])
                    env.pop('TEST_METAL_FAIL')
                    build.step_shader_libs(tree, tree / 'logs', required=True)
            self.assertEqual((tree / 'calls').read_text().splitlines(), ['a.metal'] * 4)

    def test_success_exit_with_truncated_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            tree = Path(folder)
            seed, compiler, env = self.shader_fixture(tree)
            env['TEST_METAL_TRUNCATE'] = '1'
            with patch.object(build, 'metal_compiler', return_value=(str(compiler), 'test', env)):
                with self.assertRaisesRegex(build.BuildError, 'no shader libraries'):
                    build.step_shader_libs(tree, tree / 'logs', required=True)
            self.assertFalse(list((tree / 'build/shader-libs').glob('*.metallib')))
            self.assertEqual(json.loads((seed / 'seed-manifest.json').read_text())['precompiled'], 0)

    @unittest.skipUnless(hasattr(os, 'killpg'), 'POSIX signals required')
    def test_signal_interruptions_preserve_finished_units_and_resume(self):
        worker_code = '''
import os, sys
from pathlib import Path
import build_app as build
build.metal_compiler = lambda: (sys.argv[2], 'test', dict(os.environ))
build.os.cpu_count = lambda: 1
try:
    build.step_shader_libs(Path(sys.argv[1]), Path(sys.argv[1]) / 'logs', required=True)
except KeyboardInterrupt:
    sys.exit(130)
'''
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            with self.subTest(signal=number), tempfile.TemporaryDirectory() as folder:
                tree = Path(folder)
                seed, compiler, env = self.shader_fixture(tree)
                with patch.object(build, 'metal_compiler', return_value=(str(compiler), 'test', env)):
                    build.step_shader_libs(tree, tree / 'logs', required=True)
                    first = tree / f'build/shader-libs/{build.sha256(seed / "a.metal")}.metallib'
                    first_stat = first.stat()
                    (seed / 'b.metal').write_text('// synthetic source b')
                    (seed / 'c.metal').write_text('// synthetic source c')
                    (tree / 'block').touch()
                    worker = subprocess.Popen([sys.executable, '-B', '-c', worker_code, str(tree), str(compiler)],
                                              cwd=Path(build.__file__).parent, env=env, stdout=subprocess.PIPE,
                                              stderr=subprocess.PIPE, text=True, start_new_session=True)
                    compiler_pid = None
                    try:
                        deadline = time.monotonic() + 5
                        while not (tree / 'ready').is_file() and time.monotonic() < deadline and worker.poll() is None:
                            time.sleep(0.02)
                        self.assertTrue((tree / 'ready').is_file(), 'fake compiler did not start')
                        compiler_pid = int((tree / 'ready').read_text())
                        worker.send_signal(number)
                        worker.communicate(timeout=5)
                        self.assertNotEqual(worker.returncode, 0)
                        if number != signal.SIGKILL:
                            with self.assertRaises(ProcessLookupError):
                                os.kill(compiler_pid, 0)
                        second = tree / f'build/shader-libs/{build.sha256(seed / "b.metal")}.metallib'
                        self.assertFalse(second.exists())
                    finally:
                        if worker.poll() is None:
                            os.killpg(worker.pid, signal.SIGKILL)
                        if compiler_pid is not None:
                            try:
                                os.killpg(compiler_pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        worker.communicate()
                        (tree / 'block').unlink(missing_ok=True)
                    build.step_shader_libs(tree, tree / 'logs', required=True)
                    self.assertEqual(first.stat().st_ino, first_stat.st_ino)
                    calls = (tree / 'calls').read_text().splitlines()
                    self.assertEqual(calls.count('a.metal'), 1)
                    self.assertEqual(calls.count('b.metal'), 2)
                    self.assertEqual(calls.count('c.metal'), 1)
                    self.assertEqual(json.loads((seed / 'seed-manifest.json').read_text())['precompiled'], 3)

    @unittest.skipUnless(shutil.which('xcrun'), 'macOS compiler required')
    def test_shader_installer_compiles_with_strict_warnings(self):
        root = Path(__file__).resolve().parents[1] / 'games/totk'
        for flags in ([], ['-DNSW_GAME_BOTW=1']):
            result = subprocess.run(['xcrun', 'clang', '-fsyntax-only', '-fobjc-arc', '-Wall', '-Wextra', '-Werror',
                                     '-Iapp', '-Iruntime', '-Ithird_party/d2r_gfx/gfx/nvn', *flags,
                                     'tools/install_shader_cache.m'], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_work_folder_takes_one_build_at_a_time(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder) / 'work'
            build.lock_work(work)
            with self.assertRaisesRegex(build.BuildError, 'another build or build_shaders.py is using'):
                build.lock_work(work)
            build._WORK_LOCKS.pop().close()   # the lock goes with its handle (the process exiting)
            build.lock_work(work)
            build._WORK_LOCKS.pop().close()

    def test_both_titles_remain_selectable(self):
        for title in ('botw', 'totk'):
            args = build.parse_args(['--game', title, 'base.nsp', '-k', 'prod.keys'])
            self.assertEqual(args.title, title)
            self.assertEqual(args.game, Path('base.nsp'))

    def test_offline_requires_compiler_before_touching_cache(self):
        with patch.object(build, 'metal_compiler', return_value=None), patch.object(build, 'shader_cache_lock') as lock:
            with self.assertRaisesRegex(build.BuildError, 'Metal toolchain'):
                build.main(['base.nsp', '-k', 'prod.keys', '--shaders-only'])
            lock.assert_not_called()

    def test_offline_refuses_device_cache(self):
        with self.assertRaisesRegex(build.BuildError, 'macOS cache'):
            build.main(['base.nsp', '-k', 'prod.keys', '--shaders-only', '--platform', 'ios'])

    def test_offline_botw_never_builds_or_runs_game(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            for name in ('base.nsp', 'update.nsp', 'prod.keys'):
                (root / name).write_bytes(b'PFS0')
            args = build.parse_args(['--game', 'botw', str(root / 'base.nsp'), '-k', str(root / 'prod.keys'),
                                     '--update', str(root / 'update.nsp'), '--work', str(root / 'work'), '--shaders-only'])
            tree = root / 'tree'
            methods = {name: Mock() for name in ('step_code', 'step_fonts', 'step_update_data', 'step_shaders',
                       'step_shader_libs', 'install_shader_cache', 'step_binary', 'step_app', 'run')}
            methods.update(prerequisites=Mock(return_value=Path('/usr/bin/python3')),
                           prepare_work=Mock(return_value=tree))
            with patch.multiple(build, **methods), patch('games.botw.build.shutil.disk_usage', return_value=Mock(free=64 * 2**30)):
                self.assertIs(build_botw(args, build), methods['step_shaders'].return_value)
            for name in ('step_fonts', 'step_binary', 'step_app', 'run'):
                methods[name].assert_not_called()
            methods['step_shaders'].assert_called_once_with(tree, root / 'work/logs', title='botw', all_variants=True, aoc=None)
            seed = methods['step_shaders'].return_value
            methods['step_shader_libs'].assert_called_once_with(tree, root / 'work/logs', required=True, seed=seed)
            methods['install_shader_cache'].assert_called_once_with(tree, root / 'work/logs', title='botw', seed=seed)
            self.assertEqual(methods['step_code'].call_args.kwargs, {'title': 'botw'})


if __name__ == '__main__':
    unittest.main()
