"""Build file plumbing: copy_tree (recursive clone, per-file fallback) and the BOTW seed folders."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import build_app


class CopyTree(unittest.TestCase):
    def check(self, root):
        src, dst = root / 'src', root / 'out/seed'
        (src / 'nested').mkdir(parents=True)
        (src / 'a.metal').write_text('a'); (src / 'nested/b.json').write_text('b'); (src / '.hidden.previous').write_text('h')
        (root / 'outside.bin').write_text('target'); (src / 'link.bin').symlink_to(root / 'outside.bin')
        build_app.copy_tree(src, dst)
        self.assertEqual({str(p.relative_to(dst)): p.read_text() for p in dst.rglob('*') if p.is_file()},
                         {'a.metal': 'a', 'nested/b.json': 'b', '.hidden.previous': 'h', 'link.bin': 'target'})
        self.assertFalse((dst / 'link.bin').is_symlink())   # the content, not a link out of the bundle

    def test_recursive_clone(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.check(Path(tmp))

    def test_missing_source_adds_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_app.copy_tree(Path(tmp) / 'absent', Path(tmp) / 'out/romfs-patches')
            self.assertFalse((Path(tmp) / 'out').exists())

    def test_fallback_when_the_clone_fails(self):
        real = build_app.subprocess.run
        def failing(cmd, *args, **kwargs):
            if cmd[:2] == ['cp', '-cRL']:
                return real(['false'])
            return real(cmd, *args, **kwargs)
        with tempfile.TemporaryDirectory() as tmp, patch.object(build_app.subprocess, 'run', side_effect=failing):
            self.check(Path(tmp))



class SeedFolders(unittest.TestCase):
    def test_complete_coverage_keeps_its_own_seed_folder(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / 'build/spirv-cross').mkdir(parents=True); (tree / 'build/spirv-cross/spirv-cross').write_text('x')
            translator = tree / 'prebuilt/macos-arm64/totk_xlate'; translator.parent.mkdir(parents=True); translator.write_text('t')
            (translator.parent / 'totk_xlate.sha256').write_text(hashlib.sha256(b't').hexdigest())
            with patch.object(build_app, 'run') as run:
                bounded = build_app.step_shaders(tree, tree / 'logs', title='botw')
                complete = build_app.step_shaders(tree, tree / 'logs', title='botw', all_variants=True)
            self.assertEqual((bounded.name, complete.name), ('v0', 'v0-all'))
            outputs = [call.args[0][call.args[0].index('--output') + 1] for call in run.call_args_list]
            self.assertEqual(outputs, [bounded, complete])


if __name__ == '__main__':
    unittest.main()
