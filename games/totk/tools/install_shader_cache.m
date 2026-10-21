/* CPU-only installation of a locally generated, verified shader seed. */
#include "game_version.h"
#include "game_storage.h"
#include "totk_shader_seed.h"

int main(int argc, const char **argv) {
    @autoreleasepool {
        if (argc != 2) { fprintf(stderr, "usage: install-shader-cache <local-seed>\n"); return 2; }
        NSError *error = nil;
        NSString *data = nsw_storage_prepare(NSHomeDirectory(), @NSW_GAME_FAMILY, @NSW_GAME_FOLDER, &error);
        if (!data) { fprintf(stderr, "%s\n", error.localizedDescription.UTF8String); return 1; }
        NSString *cache = nsw_shader_cache_path(data, DMSH_CHAIN, NO);
        NSDictionary *result = totk_seed_import(@(argv[1]), cache, &error);
        if (!result || ![result[@"available"] boolValue]) {
            fprintf(stderr, "%s\n", error.localizedDescription.UTF8String ?: "No verified shader seed"); return 1;
        }
        NSMutableDictionary *report = [result mutableCopy]; report[@"cache"] = cache;
        NSUInteger precompiled = 0;
        for (NSString *name in [NSFileManager.defaultManager contentsOfDirectoryAtPath:cache error:&error])
            if ([name hasSuffix:@"_b.metallib"]) precompiled++;
        report[@"precompiled"] = @(precompiled);
        NSData *json = [NSJSONSerialization dataWithJSONObject:report options:NSJSONWritingSortedKeys error:&error];
        if (!json) return 1;
        puts([[NSString alloc] initWithData:json encoding:NSUTF8StringEncoding].UTF8String);
        return 0;
    }
}
