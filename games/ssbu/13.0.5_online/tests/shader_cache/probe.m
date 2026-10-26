/* Exercise the production cache matcher and lookup; no game, shader service or GPU. */
#include "../../third_party/d2r_gfx/gfx/nvn/totk_shader_rt.m"
#include <assert.h>

int totk_depth_near_minus_w(void) { return 0; }
uint64_t dm_nvn_frame_count(void) { return 0; }
void runtime_boot_status(const char *title, const char *detail) { (void)title; (void)detail; }   // totk_rt_dir() reports the seed check
void runtime_boot_count(const char *title, uint64_t done, uint64_t total) { (void)title; (void)done; (void)total; }

int main(void) { @autoreleasepool {
    char dir[] = "/tmp/shader-cache-probe-XXXXXX"; assert(mkdtemp(dir)); setenv("TOTK_SHADER_CACHE", dir, 1);   // before the first totk_rt_dir()
    setenv("TOTK_SHADER_ASYNC", "1", 1); setenv("TOTK_SHADER_WORKERS", "1", 1);
    NSDictionary *colour = @{@"tex": @{@"0": @"*"}};
    NSDictionary *depth = @{@"tex": @{@"0": @"*_depth"}};
    assert(deps_match(colour, @{}));
    assert(deps_match(colour, @{@"tex": @{@"0": @"cube"}}));
    assert(!deps_match(colour, @{@"tex": @{@"0": @"2d_depth"}}));
    assert(deps_match(depth, @{@"tex": @{@"0": @"cube_depth"}}));
    assert(!deps_match(depth, @{@"tex": @{@"0": @"cube"}}));
    assert(!deps_match(depth, @{}));
    assert(!deps_match(colour, @{@"tex": @{@"0": @42}}));
    assert(deps_match(@{@"tex": @{@"0": @"2d"}}, @{}));
    assert(!deps_match(@{@"tex": @{@"0": @"2d"}}, @{@"tex": @{@"0": @"cube"}}));
    assert(deps_match(@{@"attr": @{@"2": @"uint"}}, @{}));
    assert(!deps_match(@{@"attr": @{@"2": @"uint"}}, @{@"attr": @{@"2": @"float"}}));
    assert(deps_match(@{@"fout": @{@"0": @"float"}}, @{}));
    assert(!deps_match(@{@"fout": @{@"0": @"uint"}}, @{}));
    assert(!deps_match(@{@"img": @{@"0": @{@"fmt": @"R32Uint"}}}, @{}));
    g_variants = [NSMutableDictionary dictionaryWithDictionary:@{
        @"shader": [NSMutableArray arrayWithObject:@{@"variant": @"cached", @"deps": colour}]}];
    g_msl_checked = [NSMutableSet setWithObject:@"cached"];
    for (int i = 0; i < 100; i++) {
        // A cache miss would read the NULL control and fail before translating.
        NSString *found = totk_rt_variant(@"shader", 1, NULL, NULL, @{@"tex": @{@"0": @"2d"}}, nil, 1);
        assert([found isEqual:@"cached"]);
    }
    // An alpha-tested draw takes only a variant translated with the same alpha test, also after the JSON index round trip.
    NSDictionary *alpha = @{@"func": @5, @"ref": @((double)0.1f)};
    NSDictionary *stored = [NSJSONSerialization JSONObjectWithData:[json_string(@{@"variant": @"alpha-cached", @"deps": colour, @"alpha": alpha})
        dataUsingEncoding:NSUTF8StringEncoding] options:0 error:nil];
    [g_variants[@"shader"] addObject:stored];
    [g_msl_checked addObject:@"alpha-cached"];
    assert([totk_rt_variant(@"shader", 1, NULL, NULL, @{@"tex": @{@"0": @"2d"}, @"alpha": alpha}, nil, 1) isEqual:@"alpha-cached"]);
    assert([totk_rt_variant(@"shader", 1, NULL, NULL, @{@"tex": @{@"0": @"2d"}}, nil, 1) isEqual:@"cached"]);
    assert(g_translated == 0 && totk_rt_completed() == 0 && !g_jobs.count);
    // Instances sharing the cache: a half-written (empty) MSL is not a finished variant, and one variant is translated
    // by one instance at a time (the lock is per open file, so a second descriptor in this process waits like another
    // instance would).
    NSString *d = totk_rt_dir(), *metal = [d stringByAppendingPathComponent:@"v_b.metal"];
    [@"" writeToFile:metal atomically:YES encoding:NSUTF8StringEncoding error:nil];
    [@"{}" writeToFile:[d stringByAppendingPathComponent:@"v_b.json"] atomically:YES encoding:NSUTF8StringEncoding error:nil];
    assert(!variant_done(@"v"));
    [@"vertex main0_out main0() {}" writeToFile:metal atomically:YES encoding:NSUTF8StringEncoding error:nil];
    assert(variant_done(@"v"));
    int held = variant_lock(@"v"); assert(held >= 0);
    int other = open([d stringByAppendingFormat:@"/translate-%lu.lock", (unsigned long)(@"v".hash % 16)].fileSystemRepresentation, O_RDWR);
    assert(other >= 0 && flock(other, LOCK_EX | LOCK_NB) == -1);
    variant_unlock(held); assert(flock(other, LOCK_EX | LOCK_NB) == 0); close(other);
    [@"vertex main0_out main0() { double x; }" writeToFile:metal atomically:YES encoding:NSUTF8StringEncoding error:nil];
    assert(!variant_done(@"v"));   // un-compilable: translated again

    // A variant another instance finished after this one read its index is queued as one that existed; the background
    // worker must record it without the shader service, which would rewrite the MSL while that instance compiles it.
    // The fake service (g_root/build/shader-service/totk_xlate) counts its requests in a file.
    char root[] = "/tmp/shader-service-probe-XXXXXX"; assert(mkdtemp(root)); g_root = @(root);
    NSString *svc = [g_root stringByAppendingPathComponent:@"build/shader-service"], *mark = [g_root stringByAppendingPathComponent:@"requests"];
    [NSFileManager.defaultManager createDirectoryAtPath:svc withIntermediateDirectories:YES attributes:nil error:nil];
    svc = [svc stringByAppendingPathComponent:@"totk_xlate"];
    [[NSString stringWithFormat:@"#!/bin/sh\nwhile read -r line; do echo x >> '%@'; echo '{\"ok\": true}'; done\n", mark]
        writeToFile:svc atomically:YES encoding:NSUTF8StringEncoding error:nil];
    chmod(svc.fileSystemRepresentation, 0755);
    uint8_t control[0x880] = {0}, code[0x50] = {0}; uint32_t c1o = 0x40, c1t = 0x50;
    memcpy(control + 0x700, &c1o, 4); memcpy(control + 0x704, &c1t, 4);
    NSArray *cases = @[@[@"done", @"fragment main0_out main0() {}", @0], @[@"broken", @"fragment main0_out main0() { double x; }", @1]];
    for (NSArray *c in cases) {
        NSDictionary *answers = @{@"tex": @{@"0": c[0]}};
        uint8_t md[32]; NSData *ad = [json_string(answers) dataUsingEncoding:NSUTF8StringEncoding]; CC_SHA256(ad.bytes, (CC_LONG)ad.length, md);
        NSString *variant = [NSString stringWithFormat:@"%@-%@", c[0], hex(md, 6)];
        [c[1] writeToFile:[d stringByAppendingFormat:@"/%@_b.metal", variant] atomically:YES encoding:NSUTF8StringEncoding error:nil];
        [@"{\"deps\": {}}" writeToFile:[d stringByAppendingFormat:@"/%@_b.json", variant] atomically:YES encoding:NSUTF8StringEncoding error:nil];
        uint64_t before = totk_rt_completed();
        assert(!totk_rt_variant(c[0], 1, control, code, answers, nil, 1));   // queued in the background
        for (int i = 0; i < 500 && totk_rt_completed() == before; i++) usleep(10000);
        assert(totk_rt_completed() == before + 1);
        assert([totk_rt_variant(c[0], 1, control, code, answers, nil, 1) isEqual:variant]);
        NSString *seen = [NSString stringWithContentsOfFile:mark encoding:NSUTF8StringEncoding error:nil];
        assert((int)(seen.length / 2) == [c[2] intValue]);
    }
    puts("pass: dependency matching and 100 cache hits without retranslation; another instance's variant not retranslated");
    return 0;
} }
