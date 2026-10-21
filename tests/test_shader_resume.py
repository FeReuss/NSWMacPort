"""Interrupted preparation reuses only durable, identity-checked shader requests; no GPU or game."""
import collections
import hashlib
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'games/totk/scripts'))
import prepare_shader_seed as seed


def inventory(romfs, modules=None):
    count = int((romfs/'count').read_text())
    shaders = {}
    for number in range(1, count+1):
        control = bytearray(0x880)
        struct.pack_into('<II', control, 0x700, 0x80, 0x100)
        shaders[f'{number:064x}'] = ('vs', bytes(control), bytes([number])*0x100, 'synthetic')
    return {}, shaders, set(shaders), []


def profiles(key, stage, reflection):
    return [dict(depth_minus_w=False), dict(tex={'0': 'cube'}), dict(tex={'0': 'cube'})]


def no_profiles(key, stage, reflection):
    return []


def lazy_profiles(key, stage, reflection):
    yield dict(depth_minus_w=False)
    receipts = Path(os.environ['SHADER_TEST_ROOT'])/'seed.checkpoints'
    assert len(list(receipts.glob('*.json'))) == 2, 'planner was consumed before the first expanded request completed'
    yield dict(tex={'0': 'cube'})


SERVICE = r'''#!/usr/bin/env python3
import json, os, signal, subprocess, sys, time
from pathlib import Path
out = Path(sys.argv[sys.argv.index('--out')+1])
chain = sys.argv[sys.argv.index('--chain')+1]
root = Path(os.environ['SHADER_TEST_ROOT'])
def note(value):
    with (root/'calls').open('a') as stream:
        stream.write(json.dumps(value)+'\n'); stream.flush(); os.fsync(stream.fileno())
for line in sys.stdin:
    request = json.loads(line); variant = request['variant']
    assert not list(out.glob('*.spv')), 'an earlier request left its scratch behind'
    (out/(variant+'.spv')).write_bytes(b'scratch')
    note(dict(variant=variant, pid=os.getpid(), clip=request['depth_minus_w']))
    blocked = root/('block' if variant.startswith('0'*63+'2') else 'block-clip')
    if blocked.exists() and (variant.startswith('0'*63+'2') or not request['depth_minus_w']):
        child = subprocess.Popen([sys.executable, '-c', 'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'])
        note(dict(child=child.pid, pid=os.getpid()))
        (out/(variant+'_b.metal')).write_text('incomplete source')
        (out/(variant+'_b.json')).write_text('{')
        while blocked.exists(): time.sleep(.02)
    if (root/'fail').exists() and variant.startswith('0'*63+'2'):
        (out/(variant+'_b.json')).write_text('{')
        print(json.dumps(dict(ok=True)), flush=True); continue
    reflection = dict(chain=chain, stage=request['stage'], deps={name: request.get(name,{}) for name in ('tex','img','attr','fout')})
    if (root/'unqueried-texture').exists(): reflection['deps']['tex'] = {}
    (out/(variant+'_b.metal')).write_text('// complete '+variant+'\n')
    (out/(variant+'_b.json')).write_text(json.dumps(reflection))
    print(json.dumps(dict(ok=True)), flush=True)
'''


def run_preparation(root, planner=False):
    with patch.object(seed.os, 'cpu_count', return_value=1):
        return seed.prepare(root/'romfs', root/'seed', root/'service', root/'spirv-cross', 'test',
                            shader_list=root/'empty-list', inventory_fn=inventory, minimum_shaders=1,
                            chain='test-chain', variant_planner=profiles if planner else None)


class ShaderResume(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root/'romfs').mkdir()
        (self.root/'romfs/index.json').write_text(json.dumps(dict(romfs_sha256='local-test')))
        (self.root/'romfs/count').write_text('3')
        (self.root/'service').write_text(SERVICE)
        (self.root/'service').chmod(0o755)
        (self.root/'spirv-cross').write_text('synthetic cross compiler')
        self.processes = []
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill(); process.wait()
        for row in self.calls():
            for name in ('pid', 'child'):
                if name in row:
                    try:
                        os.kill(row[name], signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def calls(self):
        path = self.root/'calls'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def start(self):
        log = (self.root/'runner.log').open('a')
        try:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', str(self.root)],
                env={**os.environ, 'SHADER_TEST_ROOT': str(self.root)}, stdout=log, stderr=subprocess.STDOUT)
        finally:
            log.close()
        self.processes.append(process)
        return process

    def wait_blocked(self):
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            if any('child' in row for row in self.calls()):
                receipts = list((self.root/'seed.checkpoints').glob('*.json'))
                self.assertEqual(len(receipts), 1)
                return
            time.sleep(.02)
        self.fail((self.root/'runner.log').read_text())

    def interrupt(self, number):
        (self.root/'block').touch()
        process = self.start()
        self.wait_blocked()
        os.kill(process.pid, number)
        self.assertNotEqual(process.wait(timeout=8), 0)
        self.assertFalse((self.root/'seed/seed-manifest.json').exists())
        return next(row['variant'] for row in self.calls() if 'variant' in row)

    def finish(self):
        (self.root/'block').unlink(missing_ok=True)
        (self.root/'block-clip').unlink(missing_ok=True)
        process = self.start()
        self.assertEqual(process.wait(timeout=10), 0, (self.root/'runner.log').read_text())
        report = json.loads((self.root/'seed/seed-manifest.json').read_text())
        for name, digest in report['files'].items():
            self.assertEqual(hashlib.sha256((self.root/'seed'/name).read_bytes()).hexdigest(), digest)
        return report

    def test_sigint_sigterm_and_sigkill_resume_without_retranslating_completed_requests(self):
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            with self.subTest(signal=number):
                if self.calls():
                    self.cleanup_processes()
                    for name in ('calls', 'runner.log'):
                        (self.root/name).unlink(missing_ok=True)
                    import shutil
                    for name in ('seed', 'seed.partial', 'seed.checkpoints'):
                        shutil.rmtree(self.root/name, ignore_errors=True)
                first = self.interrupt(number)
                report = self.finish()
                counts = collections.Counter(row['variant'] for row in self.calls() if 'variant' in row)
                self.assertEqual(counts[first], 1)
                self.assertEqual(report['shaders'], 3)
                if number != signal.SIGKILL:
                    for row in self.calls():
                        if 'child' in row:
                            # A killed descendant can remain a zombie until launchd reaps it.
                            status = subprocess.run(['ps', '-o', 'stat=', '-p', str(row['child'])], capture_output=True, text=True).stdout.strip()
                            self.assertTrue(not status or status.startswith('Z'), status)

    def test_truncated_payload_or_receipt_is_never_reused(self):
        for damage in ('payload', 'receipt', 'index'):
            with self.subTest(damage=damage):
                if self.calls():
                    self.cleanup_processes()
                    import shutil
                    for name in ('seed', 'seed.partial', 'seed.checkpoints'):
                        shutil.rmtree(self.root/name, ignore_errors=True)
                    (self.root/'calls').unlink()
                first = self.interrupt(signal.SIGTERM)
                names = {'payload': 'seed.partial/'+first+'_b.metal', 'receipt': 'seed.checkpoints/'+first+'.json',
                         'index': 'seed.partial/'+first[:64]+'.variants'}
                path = self.root/names[damage]
                path.write_text('{')
                self.finish()
                self.assertEqual(sum(row.get('variant') == first for row in self.calls()), 1 if damage == 'index' else 2)

    def test_new_listed_requests_preserve_completed_partial_requests(self):
        first = self.interrupt(signal.SIGTERM)
        answers = {**seed.ANSWERS, 'tex': {'0': 'cube'}}
        (self.root/'empty-list').write_text('V\t'+first[:64]+'\t'+json.dumps(answers, sort_keys=True, separators=(',', ':'))+'\n')
        report = self.finish()
        self.assertEqual(sum(row.get('variant') == first for row in self.calls()), 1)
        self.assertEqual(report['shaders'], 4)

    def test_completed_variant_survives_interruption_within_one_shader_group(self):
        (self.root/'romfs/count').write_text('1')
        (self.root/'profiles').touch(); (self.root/'block-clip').touch()
        process = self.start(); self.wait_blocked()
        first = next(row['variant'] for row in self.calls() if 'variant' in row)
        os.kill(process.pid, signal.SIGTERM); process.wait(timeout=8)
        report = self.finish()
        self.assertEqual(sum(row.get('variant') == first for row in self.calls()), 1)
        self.assertEqual(report['shaders'], 3)

    def test_changed_translator_invalidates_completed_request(self):
        first = self.interrupt(signal.SIGTERM)
        with (self.root/'service').open('a') as stream:
            stream.write('\n# new producer\n')
        self.finish()
        self.assertEqual(sum(row.get('variant') == first for row in self.calls()), 2)

    def test_error_keeps_previous_output_and_signal_handlers(self):
        output = self.root/'seed'
        output.mkdir(); (output/'keep').write_text('previous valid output')
        (output/'seed-manifest.json').write_text(json.dumps(dict(chain='previous')))
        before = {path.name: path.read_bytes() for path in output.iterdir()}
        (self.root/'fail').touch()
        handlers = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        with patch.dict(os.environ, SHADER_TEST_ROOT=str(self.root)), self.assertRaises(ValueError):
            run_preparation(self.root)
        self.assertEqual({path.name: path.read_bytes() for path in output.iterdir()}, before)
        self.assertEqual({number: signal.getsignal(number) for number in handlers}, handlers)

    def test_publication_error_restores_previous_output(self):
        output = self.root/'seed'
        output.mkdir(); (output/'keep').write_text('previous output')
        original_rename = Path.rename
        def fail_publish(path, target):
            if path == self.root/'seed.partial':
                raise OSError('simulated publication failure')
            return original_rename(path, target)
        with patch.dict(os.environ, SHADER_TEST_ROOT=str(self.root)), patch.object(Path, 'rename', fail_publish):
            with self.assertRaisesRegex(OSError, 'publication failure'):
                run_preparation(self.root)
        self.assertEqual((output/'keep').read_text(), 'previous output')
        self.assertFalse((output/'seed-manifest.json').exists())
        self.assertFalse((self.root/'seed.previous').exists())

    def test_complete_seed_bootstraps_old_requests_when_coverage_expands(self):
        (self.root/'romfs/count').write_text('1')
        with patch.dict(os.environ, SHADER_TEST_ROOT=str(self.root)):
            run_preparation(self.root)
            marker = self.root/'seed/seed-manifest.json'
            report = json.loads(marker.read_text()); report['preparation_sha256'] = 'older-script'
            marker.write_text(json.dumps(report))
            import shutil
            shutil.rmtree(self.root/'seed.checkpoints')
            expanded = run_preparation(self.root, planner=True)
        rows = [row for row in self.calls() if 'variant' in row]
        self.assertEqual(len(rows), 3)   # default once, new clip mode once, cube once
        self.assertEqual(expanded['shaders'], 3)
        index = json.loads(next((self.root/'seed').glob('*.variants')).read_text())
        self.assertEqual(sorted(row['depth_minus_w'] for row in index), [False, True, True])

    def test_bounded_seed_is_not_reused_for_complete_coverage(self):
        (self.root/'romfs/count').write_text('1')
        with patch.dict(os.environ, SHADER_TEST_ROOT=str(self.root)), patch.object(seed.os, 'cpu_count', return_value=1):
            bounded = seed.prepare(self.root/'romfs', self.root/'seed', self.root/'service', self.root/'spirv-cross', 'test',
                                   shader_list=self.root/'empty-list', inventory_fn=inventory, minimum_shaders=1,
                                   chain='test-chain', variant_planner=no_profiles)
            complete = run_preparation(self.root, planner=True)
        self.assertEqual((bounded['shaders'], complete['shaders']), (1, 3))
        self.assertEqual(len([row for row in self.calls() if 'variant' in row]), 3)   # the default request is resumed

    def test_receipts_the_plan_no_longer_requests_are_pruned_after_publication(self):
        (self.root/'romfs/count').write_text('1')
        with patch.dict(os.environ, SHADER_TEST_ROOT=str(self.root)):
            run_preparation(self.root, planner=True)
            receipts = self.root/'seed.checkpoints'
            self.assertEqual(len(list(receipts.glob('*.json'))), 3)
            marker = self.root/'seed/seed-manifest.json'
            old = json.loads(marker.read_text()); old['preparation_sha256'] = 'older-script'; marker.write_text(json.dumps(old))
            report = run_preparation(self.root)   # the default request only
        self.assertEqual(report['shaders'], 1)
        self.assertEqual(len(list(receipts.glob('*.json'))), 1)

    def test_resumed_files_are_copied_when_hard_links_fail(self):
        (self.root/'romfs/count').write_text('1')
        with patch.dict(os.environ, SHADER_TEST_ROOT=str(self.root)):
            run_preparation(self.root, planner=True)
            marker = self.root/'seed/seed-manifest.json'
            old = json.loads(marker.read_text()); old['preparation_sha256'] = 'older-script'; marker.write_text(json.dumps(old))
            with patch.object(seed.os, 'link', side_effect=OSError('no hard links here')):
                report = run_preparation(self.root, planner=True)
        self.assertEqual((report['shaders'], report['resumed_variants']), (3, 3))
        self.assertEqual(len([row for row in self.calls() if 'variant' in row]), 3)   # nothing translated twice

    def test_planner_is_consumed_lazily_after_each_request_completes(self):
        (self.root/'romfs/count').write_text('1')
        with patch.dict(os.environ, SHADER_TEST_ROOT=str(self.root)), patch.object(seed.os, 'cpu_count', return_value=1):
            report = seed.prepare(self.root/'romfs', self.root/'seed', self.root/'service', self.root/'spirv-cross', 'test',
                                  shader_list=self.root/'empty-list', inventory_fn=inventory, minimum_shaders=1,
                                  chain='test-chain', variant_planner=lazy_profiles)
        self.assertEqual(report['shaders'], 3)

    def test_redundant_requests_have_receipts_and_are_not_translated_again(self):
        (self.root/'romfs/count').write_text('1')
        (self.root/'unqueried-texture').touch()
        with patch.dict(os.environ, SHADER_TEST_ROOT=str(self.root)):
            report = run_preparation(self.root, planner=True)
            self.assertEqual(report['shaders'], 2)
            marker = self.root/'seed/seed-manifest.json'
            old = json.loads(marker.read_text()); old['preparation_sha256'] = 'older-script'
            marker.write_text(json.dumps(old))
            report = run_preparation(self.root, planner=True)
        self.assertEqual(len([row for row in self.calls() if 'variant' in row]), 3)
        self.assertEqual(report['shaders'], 2)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        directory = Path(sys.argv[2])
        run_preparation(directory, planner=(directory/'profiles').exists())
    else:
        unittest.main()
