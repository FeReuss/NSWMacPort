/* Portable player data. Migration copies to a staging directory and keeps the old data intact. */
#pragma once
#import <Foundation/Foundation.h>

static BOOL nsw_copy_if_present(NSString *source, NSString *destination, NSError **error) {
    NSFileManager *fm = NSFileManager.defaultManager;
    return ![fm fileExistsAtPath:source] || [fm copyItemAtPath:source toPath:destination error:error];
}

/* The ~150,000-file shader cache. Spotlight skips folders named *.noindex (as Xcode's Intermediates.noindex); indexing this
   one after each build kept the efficiency cores and the disk busy for hours while the game ran, with its long frames up
   (2026-10-21). migrate renames the folder of earlier builds; only the launcher does it, the build may run beside a game. */
static inline NSString *nsw_shader_cache_path(NSString *data, const char *chain, BOOL migrate) {
    NSString *old = [data stringByAppendingPathComponent:[NSString stringWithFormat:@"Shaders Cache/rt-%s", chain]];
    NSString *path = [old stringByAppendingString:@".noindex"];
    NSFileManager *fm = NSFileManager.defaultManager;
    if (![fm fileExistsAtPath:path] && [fm fileExistsAtPath:old] &&
        (!migrate || rename(old.fileSystemRepresentation, path.fileSystemRepresentation) != 0)) return old;
    return path;
}

static NSString *nsw_storage_prepare(NSString *home, NSString *family, NSString *folder, NSError **error) {
    NSFileManager *fm = NSFileManager.defaultManager;
    const char *override = getenv("NSW_GAMES_DIR");
    NSString *games = override && *override ? @(override) : [home stringByAppendingPathComponent:@"Documents/NSW Games"];
    NSString *data = [games stringByAppendingPathComponent:folder];
    if (![fm createDirectoryAtPath:games withIntermediateDirectories:YES attributes:nil error:error]) return nil;
    if (![fm fileExistsAtPath:data]) {
        NSString *stage = [games stringByAppendingPathComponent:[@".migration-" stringByAppendingString:NSUUID.UUID.UUIDString]];
        NSString *legacy = [home stringByAppendingPathComponent:[@"Library/Application Support/MacNativeGames/" stringByAppendingString:family]];
        NSString *oldProfile = [legacy stringByAppendingPathComponent:@"profile"];
        BOOL ok = [fm createDirectoryAtPath:stage withIntermediateDirectories:NO attributes:nil error:error];
        NSString *settings = [stage stringByAppendingPathComponent:@"Settings"];
        if (ok) ok = [fm createDirectoryAtPath:settings withIntermediateDirectories:NO attributes:nil error:error];
        if (ok && [fm fileExistsAtPath:oldProfile]) {
            NSArray *names = [fm contentsOfDirectoryAtPath:oldProfile error:error];
            ok = names != nil;
            for (NSString *name in names) {
                if ([@[@"save", @"temp", @"cache", @"pso_user.tsv"] containsObject:name]) continue;
                if (!nsw_copy_if_present([oldProfile stringByAppendingPathComponent:name], [settings stringByAppendingPathComponent:name], error)) { ok = NO; break; }
            }
        }
        NSDictionary *copies = @{@"profile/save": @"Saves", @"shader-cache": @"Shaders Cache", @"mods": @"Mods"};
        for (NSString *name in copies) if (ok)
            ok = nsw_copy_if_present([legacy stringByAppendingPathComponent:name], [stage stringByAppendingPathComponent:copies[name]], error);
        NSString *shaders = [stage stringByAppendingPathComponent:@"Shaders Cache"];
        if (ok) ok = [fm createDirectoryAtPath:shaders withIntermediateDirectories:YES attributes:nil error:error];
        if (ok) ok = nsw_copy_if_present([oldProfile stringByAppendingPathComponent:@"pso_user.tsv"], [shaders stringByAppendingPathComponent:@"pso_user.tsv"], error);
        NSString *controls = [settings stringByAppendingPathComponent:@"controls.txt"];
        if (ok && ![fm fileExistsAtPath:controls]) ok = nsw_copy_if_present([legacy stringByAppendingPathComponent:@"controls.txt"], controls, error);
        if (ok) ok = [fm moveItemAtPath:stage toPath:data error:error];
        if (!ok) { [fm removeItemAtPath:stage error:nil]; return nil; }
    }
    NSString *manifest = [data stringByAppendingPathComponent:@"game.json"];
    if ([fm fileExistsAtPath:manifest]) {
        NSData *raw = [NSData dataWithContentsOfFile:manifest options:0 error:error];
        NSDictionary *old = raw ? [NSJSONSerialization JSONObjectWithData:raw options:0 error:error] : nil;
        if (![old isKindOfClass:NSDictionary.class] || ![old[@"game"] isEqual:family]) {
            if (error) *error = [NSError errorWithDomain:@"MacNativeGames" code:1 userInfo:@{NSLocalizedDescriptionKey: @"The player-data folder belongs to another game or has an invalid game.json."}];
            return nil;
        }
    }
    for (NSString *name in @[@"Saves", @"Shaders Cache", @"Screenshots", @"Settings", @"Mods", @"Logs", @"Cache"])
        if (![fm createDirectoryAtPath:[data stringByAppendingPathComponent:name] withIntermediateDirectories:YES attributes:nil error:error]) return nil;
    if (![fm fileExistsAtPath:manifest]) {
        NSData *raw = [NSJSONSerialization dataWithJSONObject:@{@"schema": @1, @"game": family, @"name": folder} options:NSJSONWritingPrettyPrinted error:error];
        if (!raw || ![raw writeToFile:manifest options:NSDataWritingAtomic error:error]) return nil;
    }
    return data;
}
