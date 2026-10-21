"""Exercise the production warm-up planner without creating a GPU device."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WarmPolicy(unittest.TestCase):
    def test_expanded_botw_libraries_stay_on_disk_and_totk_keeps_its_policy(self):
        source = (ROOT / 'games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m').read_text()
        planner = source[source.index('static BOOL warm_full_cache('):source.index('static void warm_task_done(')]
        harness = '''#import <Foundation/Foundation.h>
#include <stdint.h>
#include <assert.h>
typedef struct { char vs[80], fs[80]; } pso_skey;
static int pso_skey_parse(const char *s, pso_skey *k) { return 1; }
static int warm_available(NSString *key) { return 1; }
static NSString *warm_key(const char *s, size_t n) { return @""; }
''' + planner + '''
int main(int argc, char **argv) { @autoreleasepool {
    NSString *root = @(argv[1]);
    NSString *seed = [root stringByAppendingPathComponent:@"shader-seed/pso_manifest.tsv"];
    NSString *user = [root stringByAppendingPathComponent:@"pso_user.tsv"];
    NSMutableSet *keys = [NSMutableSet set], *eligible = [NSMutableSet set];
    uint64_t tasks, shaders, pipelines;
    warm_plan(@[seed, user], @[], keys, eligible, &tasks, &shaders, &pipelines);
#ifdef NSW_GAME_BOTW
    assert(!warm_full_cache());
    assert(tasks == 3 && shaders == 3 && pipelines == 1);
    assert(![keys containsObject:@"offline_b"]);
#else
    assert(warm_full_cache());
    assert(tasks == 4 && shaders == 4 && pipelines == 1);
    assert([keys containsObject:@"offline_b"]);
#endif
    assert([keys containsObject:@"compute_b"]);
    assert([keys containsObject:@"used_b"]);
    return 0;
} }
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'shader-seed').mkdir()
            (root / 'shader-seed/pso_manifest.tsv').write_text('S\toffline_b\t1\nC\tcompute_b\n')
            (root / 'pso_user.tsv').write_text('S\tused_b\t0\nS\tfragment_b\t1\nC\tcompute_b\n')
            path = root / 'check.m'
            path.write_text(harness)
            for flags in ([], ['-DNSW_GAME_BOTW=1']):
                subprocess.run(['xcrun', 'clang', '-fobjc-arc', *flags, str(path), '-framework', 'Foundation',
                                '-o', str(root / 'check')], check=True, capture_output=True)
                subprocess.run([root / 'check', root], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
