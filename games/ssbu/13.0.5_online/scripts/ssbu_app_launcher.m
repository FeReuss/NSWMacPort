/* SSBU.app launcher (scripts/package_native_app.py builds the bundle around it).
   Everything the game reads is inside the bundle (Contents/Resources/game: runtime, shader tools, rewritten modules and
   NROs, RomFS, shared fonts); everything it writes is in "SSBU Data" next to the app (saves, shader cache, log, server
   list), so the app keeps working when the project checkout is not there. Contents/MacOS/ssbu-native is a symlink to the
   bundled runtime: started through it, the window belongs to this app (name and icon), while the runtime still finds its
   shader tools next to the real file. The game runs as the user's (TOTK_SLOT_OWNER=user), so it takes the one-game slot
   ahead of test runs. Sessions have no time limit.

   Every launch first shows a menu (ServerPicker): play offline, or pick an ssbu-oof server (ssbu-oof docs/STANDARD.md
   sections 1-2; add, edit, delete), sign in or create an account on it, then Start Game, Friends and Requests.
   "SSBU Data/servers.json" keeps the list and, per server, the user name and, with "Remember me", the account's pid and
   device key (never the password: STANDARD.md 2); the game gets the chosen one as OOF_SERVER / OOF_TLS / OOF_PID /
   OOF_DEVICE_KEY / OOF_USERNAME (runtime/oof_client.m, account.c). Offline: none of them is set.

   DLC: "Add DLC…" (or dropping NSP files / folders on the list) reads the player's DLC NSPs, which are licences only,
   keeps their indices in "SSBU Data/dlc.json" and passes them to the game as SSBU_AOC (runtime/services.c). */
#import <Cocoa/Cocoa.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#ifndef SHADER_CHAIN
#error "build with -DSHADER_CHAIN=\"rt-<DMSH_CHAIN>\" (scripts/package_native_app.py)"
#endif

static void join(char *out, const char *dir, const char *name) {
    if (snprintf(out, PATH_MAX, "%s/%s", dir, name) >= PATH_MAX) { fputs("path too long\n", stderr); exit(1); }
}
static void parent_of(char *out, const char *path) {
    char copy[PATH_MAX]; snprintf(copy, sizeof copy, "%s", path); snprintf(out, PATH_MAX, "%s", dirname(copy));
}
static int run(const char *const argv[]) {   /* a system tool, waited for */
    pid_t pid = fork();
    if (pid == 0) { execv(argv[0], (char *const *)argv); _exit(127); }
    int status = 0; if (pid < 0 || waitpid(pid, &status, 0) < 0) return -1;
    return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}
static int newest_first(const void *a, const void *b) { return strcmp(*(char *const *)b, *(char *const *)a); }
/* Saves are rewritten in place (runtime/fs_host.c), so a crash during a write could leave the only copy damaged: keep the
   saves as they were before each of the last five sessions in "SSBU Data/backups/<date-time>". */
static void back_up_saves(const char *profile, const char *data, const char *stamp) {
    char saves[PATH_MAX], backups[PATH_MAX], target[PATH_MAX]; struct stat st;
    join(saves, profile, "save"); join(backups, data, "backups");
    if (stat(saves, &st) || !S_ISDIR(st.st_mode)) return;
    mkdir(backups, 0700); join(target, backups, stamp);
    if (run((const char *const[]){"/bin/cp", "-Rp", saves, target, NULL})) return;
    DIR *d = opendir(backups); if (!d) return;
    char *names[256]; int n = 0; struct dirent *e;
    while ((e = readdir(d)) && n < 256) if (e->d_name[0] != '.') names[n++] = strdup(e->d_name);
    closedir(d);
    qsort(names, (size_t)n, sizeof names[0], newest_first);   /* stamps sort by time */
    for (int i = 0; i < n; i++) {
        if (i >= 5) { char old[PATH_MAX]; join(old, backups, names[i]); run((const char *const[]){"/bin/rm", "-rf", old, NULL}); }
        free(names[i]);
    }
}

/* ---------- server list ---------- */

/* "host:port" as the runtime parses OOF_SERVER (net_host.c), or nil. Accepts a pasted URL (http/https/ws/wss sets *tls)
   and a missing port (443 with TLS, 80 without). shortcut: IPv6 literals are rejected, add bracket parsing if a server
   needs one. */
static NSString *normalize_address(NSString *text, BOOL *tls) {
    NSString *s = [text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    for (NSString *scheme in @[@"https://", @"wss://", @"http://", @"ws://"])
        if ([s.lowercaseString hasPrefix:scheme]) { *tls = [scheme hasPrefix:@"https"] || [scheme hasPrefix:@"wss"]; s = [s substringFromIndex:scheme.length]; }
    while ([s hasSuffix:@"/"]) s = [s substringToIndex:s.length - 1];
    NSArray<NSString *> *parts = [s componentsSeparatedByString:@":"];
    if (parts.count > 2 || !parts[0].length || parts[0].length > 253) return nil;
    NSCharacterSet *bad = [NSCharacterSet characterSetWithCharactersInString:
        @"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"].invertedSet;
    if ([parts[0] rangeOfCharacterFromSet:bad].location != NSNotFound) return nil;
    NSInteger port = *tls ? 443 : 80;
    if (parts.count == 2) {
        if (!parts[1].length || [parts[1] rangeOfCharacterFromSet:NSCharacterSet.decimalDigitCharacterSet.invertedSet].location != NSNotFound) return nil;
        port = parts[1].integerValue;
    }
    if (port < 1 || port > 65535) return nil;
    return [NSString stringWithFormat:@"%@:%ld", parts[0].lowercaseString, (long)port];
}

/* A device login as the server issued it (STANDARD.md 2.2): pid below 2^53, device key in hex. */
static BOOL valid_account(id pid, id password) {
    if (![pid isKindOfClass:NSNumber.class] || ![password isKindOfClass:NSString.class]) return NO;
    if (CFGetTypeID((__bridge CFTypeRef)pid) == CFBooleanGetTypeID()) return NO;   /* JSON true is an NSNumber too */
    unsigned long long p = [pid unsignedLongLongValue];
    if (!p || p >= (1ULL << 53) || [pid doubleValue] != (double)p) return NO;
    NSString *pw = password;
    NSCharacterSet *hex = [NSCharacterSet characterSetWithCharactersInString:@"0123456789abcdefABCDEF"];
    return pw.length >= 16 && pw.length <= 256 && [pw rangeOfCharacterFromSet:hex.invertedSet].location == NSNotFound;
}

/* An account's user name (STANDARD.md 2): 3-10 letters, digits, _ - or . -- also the player's nickname in the game. */
static BOOL valid_username(id name) {
    if (![name isKindOfClass:NSString.class] || [name length] < 3 || [name length] > 10) return NO;
    NSCharacterSet *ok = [NSCharacterSet characterSetWithCharactersInString:
        @"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."];
    return [name rangeOfCharacterFromSet:ok.invertedSet].location == NSNotFound;
}

/* servers.json: {"servers": [{"name", "address", "tls", "username"?, "remember"?, "pid"?, "device_key"?, "ca"?}],
   "last": "<address>"}. Never a password: logging in leaves only the device key, which the server can revoke, and only
   with "Remember me" (an entry from before that option, with a key and no "remember", counts as remembered). The user
   name stays to fill in the sign-in screen. Entries that do not validate are dropped (the file is the user's, and may
   be hand-edited). */
static NSMutableArray<NSMutableDictionary *> *load_servers(NSString *path, NSString **last) {
    NSMutableArray *out = [NSMutableArray array];
    NSData *data = [NSData dataWithContentsOfFile:path];
    NSDictionary *root = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
    if (![root isKindOfClass:NSDictionary.class]) return out;
    if (last && [root[@"last"] isKindOfClass:NSString.class]) *last = root[@"last"];
    NSArray *list = [root[@"servers"] isKindOfClass:NSArray.class] ? root[@"servers"] : @[];
    for (NSDictionary *e in list) {
        if (![e isKindOfClass:NSDictionary.class] || ![e[@"name"] isKindOfClass:NSString.class] || ![e[@"address"] isKindOfClass:NSString.class]) continue;
        BOOL tls = [e[@"tls"] isKindOfClass:NSNumber.class] && [e[@"tls"] boolValue];
        NSString *address = normalize_address(e[@"address"], &tls);
        NSString *name = [e[@"name"] stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
        if (!address || !name.length) continue;
        NSMutableDictionary *s = [@{@"name": name, @"address": address, @"tls": @(tls)} mutableCopy];
        if (valid_username(e[@"username"])) s[@"username"] = e[@"username"];
        BOOL remember = ![e[@"remember"] isKindOfClass:NSNumber.class] || [e[@"remember"] boolValue];
        if (remember && s[@"username"] && valid_account(e[@"pid"], e[@"device_key"])) {
            s[@"pid"] = e[@"pid"]; s[@"device_key"] = e[@"device_key"]; s[@"remember"] = @YES;
        }
        if ([e[@"ca"] isKindOfClass:NSString.class] && [e[@"ca"] length]) s[@"ca"] = e[@"ca"];
        [out addObject:s];
    }
    return out;
}

/* The file holds account passwords: owner-only, written whole (a crash leaves the old list, not half of the new one). */
static BOOL save_servers(NSString *path, NSArray *servers, NSString *last) {
    NSMutableArray *kept = [NSMutableArray array];   /* a session without "Remember me" lives only in memory */
    for (NSDictionary *s in servers) {
        NSMutableDictionary *e = [s mutableCopy];
        if (![e[@"remember"] boolValue]) [e removeObjectsForKeys:@[@"pid", @"device_key", @"remember"]];
        [kept addObject:e];
    }
    NSMutableDictionary *root = [@{@"servers": kept} mutableCopy];
    if (last) root[@"last"] = last;
    NSData *data = [NSJSONSerialization dataWithJSONObject:root options:NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys error:nil];
    NSString *tmp = [path stringByAppendingString:@".partial"];
    if (!data || ![NSFileManager.defaultManager createFileAtPath:tmp contents:data attributes:@{NSFilePosixPermissions: @0600}]) return NO;
    return rename(tmp.fileSystemRepresentation, path.fileSystemRepresentation) == 0;
}

static NSURL *server_url(NSDictionary *s, NSString *api) {
    return [NSURL URLWithString:[NSString stringWithFormat:@"%@://%@%@", [s[@"tls"] boolValue] ? @"https" : @"http", s[@"address"], api]];
}

/* A server's own certificate authority (STANDARD.md 1, oof.ca): the certificates of the PEM file the player picked, or nil
   (no file, or no certificate in it). The launcher's requests and the game (OOF_CA) then trust only those for it. */
static NSArray *ca_certificates(NSString *path) {
    NSData *pem = path.length ? [NSData dataWithContentsOfFile:path] : nil;
    SecExternalFormat format = kSecFormatPEMSequence; SecExternalItemType type = kSecItemTypeCertificate; CFArrayRef items = NULL;
    if (!pem || SecItemImport((__bridge CFDataRef)pem, NULL, &format, &type, 0, NULL, NULL, &items) || !items) return nil;
    NSMutableArray *certs = [NSMutableArray array];
    for (id item in (__bridge_transfer NSArray *)items) if (CFGetTypeID((__bridge CFTypeRef)item) == SecCertificateGetTypeID()) [certs addObject:item];
    return certs.count ? certs : nil;
}
static BOOL trusted_by(SecTrustRef trust, NSArray *anchors) {
    SecTrustSetAnchorCertificates(trust, (__bridge CFArrayRef)anchors); SecTrustSetAnchorCertificatesOnly(trust, true);
    return SecTrustEvaluateWithError(trust, NULL);
}
@interface ServerTrust : NSObject <NSURLSessionDelegate>
@property NSArray *anchors;
@end
@implementation ServerTrust
- (void)URLSession:(NSURLSession *)session didReceiveChallenge:(NSURLAuthenticationChallenge *)c
 completionHandler:(void (^)(NSURLSessionAuthChallengeDisposition, NSURLCredential *))done {
    (void)session; SecTrustRef trust = c.protectionSpace.serverTrust;
    if (![c.protectionSpace.authenticationMethod isEqual:NSURLAuthenticationMethodServerTrust] || !trust) done(NSURLSessionAuthChallengePerformDefaultHandling, nil);
    else if (trusted_by(trust, self.anchors)) done(NSURLSessionAuthChallengeUseCredential, [NSURLCredential credentialForTrust:trust]);
    else done(NSURLSessionAuthChallengeCancelAuthenticationChallenge, nil);
}
@end
/* One request to a server, through a session that trusts only its own CA when it has one. */
static void server_request(NSDictionary *s, NSURLRequest *r, void (^done)(NSData *, NSURLResponse *, NSError *)) {
    NSArray *anchors = ca_certificates(s[@"ca"]); NSURLSession *session = NSURLSession.sharedSession;
    if (anchors) {
        ServerTrust *t = [ServerTrust new]; t.anchors = anchors;
        session = [NSURLSession sessionWithConfiguration:NSURLSessionConfiguration.ephemeralSessionConfiguration delegate:t delegateQueue:nil];
    }
    [[session dataTaskWithRequest:r completionHandler:done] resume];
    if (anchors) [session finishTasksAndInvalidate];
}

/* An SSBU DLC NSP: title 0x01006A800016F000 + index. An NSP (PFS0) names its ticket "<rights id>.tik" and a rights id
   starts with the title id, so the index is read without keys. 0: not one (another game's content, not an NSP). */
static int dlc_index(NSString *path) {
    NSFileHandle *f = [NSFileHandle fileHandleForReadingAtPath:path];
    NSData *head = f ? [f readDataUpToLength:0x10000 error:nil] : nil; [f closeFile];
    const uint8_t *b = head.bytes; size_t n = head.length;
    if (n < 16 || memcmp(b, "PFS0", 4)) return 0;
    uint32_t files, strings; memcpy(&files, b + 4, 4); memcpy(&strings, b + 8, 4);
    size_t table = 16 + 24 * (size_t)files;
    if (files > 64 || table + strings > n) return 0;
    for (uint32_t i = 0; i < files; i++) {
        uint32_t at; memcpy(&at, b + 16 + 24 * i + 16, 4);
        if (at >= strings) return 0;
        const char *name = (const char *)b + table + at; size_t len = strnlen(name, strings - at);
        if (len != 36 || strcmp(name + 32, ".tik")) continue;
        char tid[17]; memcpy(tid, name, 16); tid[16] = 0;
        char *end; unsigned long long t = strtoull(tid, &end, 16);
        if (*end || (t & ~0xfffULL) != 0x01006A800016F000ULL || !(t & 0xfff)) return 0;
        return (int)(t & 0xfff);
    }
    return 0;
}
/* Files, and the files directly inside folders, as indices; what is not SSBU DLC is reported by name. */
static NSMutableSet<NSNumber *> *dlc_scan(NSArray<NSString *> *paths, NSMutableArray<NSString *> *rejected) {
    NSMutableSet *found = [NSMutableSet set]; NSFileManager *fm = NSFileManager.defaultManager;
    for (NSString *path in paths) {
        BOOL dir = NO; if (![fm fileExistsAtPath:path isDirectory:&dir]) continue;
        NSArray *files = dir ? [fm contentsOfDirectoryAtPath:path error:nil] : @[path.lastPathComponent];
        for (NSString *name in files) {
            if ([name hasPrefix:@"."]) continue;
            NSString *file = dir ? [path stringByAppendingPathComponent:name] : path; BOOL sub = NO;
            if ([fm fileExistsAtPath:file isDirectory:&sub] && sub) continue;
            int index = dlc_index(file);
            if (index) [found addObject:@(index)]; else [rejected addObject:name];
        }
    }
    return found;
}
/* dlc.json: {"indices": [n, …]}, sorted. */
static NSMutableSet<NSNumber *> *load_dlc(NSString *path) {
    NSMutableSet *out = [NSMutableSet set]; NSData *data = [NSData dataWithContentsOfFile:path];
    NSDictionary *root = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
    NSArray *list = [root isKindOfClass:NSDictionary.class] && [root[@"indices"] isKindOfClass:NSArray.class] ? root[@"indices"] : @[];
    for (NSNumber *i in list)
        if ([i isKindOfClass:NSNumber.class] && i.doubleValue == i.intValue && i.intValue >= 1 && i.intValue <= 0xfff) [out addObject:@(i.intValue)];
    return out;
}
static BOOL save_dlc(NSString *path, NSSet<NSNumber *> *indices) {
    NSArray *sorted = [indices.allObjects sortedArrayUsingSelector:@selector(compare:)];
    NSData *data = [NSJSONSerialization dataWithJSONObject:@{@"indices": sorted} options:NSJSONWritingPrettyPrinted error:nil];
    NSString *tmp = [path stringByAppendingString:@".partial"];
    return data && [data writeToFile:tmp atomically:NO] && rename(tmp.fileSystemRepresentation, path.fileSystemRepresentation) == 0;
}

enum { CHOICE_QUIT = -2, CHOICE_OFFLINE = -1 };

/* One window, four screens (user request 2026-10-26): Start (offline, or a server) → Servers (add, edit, delete) →
   the server's sign-in / new account → signed in (tabs Play, Friends, Requests). Only "Start Game" starts the game. */
@interface ServerPicker : NSObject <NSTableViewDataSource, NSTableViewDelegate, NSWindowDelegate, NSTabViewDelegate>
@property NSString *path, *last, *dlcPath;
@property NSMutableArray<NSMutableDictionary *> *servers;
@property NSMutableDictionary<NSString *, NSString *> *status;   /* address -> status line */
@property NSWindow *window;
@property NSTableView *table, *friendsTable, *requestsTable;
@property NSButton *choose, *edit, *remove, *startButton, *signInButton, *createButton, *rememberBox;
@property NSInteger row;                        /* the server being signed in to / played on */
@property NSArray<NSDictionary *> *friendRows;  /* {kind: friend|blocked, pid, username, online} */
@property NSArray<NSDictionary *> *requestRows; /* {kind: incoming|outgoing, pid, username} */
@property NSTextField *note, *caField, *accountTitle, *accountNote, *userField, *homeTitle, *homeNote, *friendsNote, *requestsNote;
@property NSSecureTextField *passwordField;
@property NSInteger choice;
@end

@implementation ServerPicker
- (NSButton *)button:(NSString *)title action:(SEL)action {
    NSButton *b = [NSButton buttonWithTitle:title target:self action:action];
    b.translatesAutoresizingMaskIntoConstraints = NO;
    return b;
}
- (NSTextField *)label:(NSString *)text size:(CGFloat)size {
    NSTextField *l = [NSTextField wrappingLabelWithString:text];
    l.font = size > 13 ? [NSFont boldSystemFontOfSize:size] : [NSFont systemFontOfSize:size];
    if (size <= 13) l.textColor = NSColor.secondaryLabelColor;
    return l;
}
/* A screen: views stacked from the top, a row of buttons at the bottom. `fill` (a list or the tabs) takes the height
   left over; without one the views form a column centred in the window, so a large window leaves no empty half. */
- (NSView *)screen:(NSArray<NSView *> *)views buttons:(NSArray<NSView *> *)buttons fill:(NSView *)fill {
    NSView *v = [[NSView alloc] init];
    NSStackView *top = [NSStackView stackViewWithViews:views];
    top.orientation = NSUserInterfaceLayoutOrientationVertical; top.alignment = NSLayoutAttributeLeading; top.spacing = 10;
    top.translatesAutoresizingMaskIntoConstraints = NO; [v addSubview:top];
    NSLayoutYAxisAnchor *floor = v.bottomAnchor; CGFloat gap = -20;
    if (buttons.count) {   /* an empty row would still claim its gap */
        NSStackView *row = [NSStackView stackViewWithViews:buttons]; row.spacing = 8;
        row.translatesAutoresizingMaskIntoConstraints = NO; [v addSubview:row];
        [row.trailingAnchor constraintEqualToAnchor:v.trailingAnchor constant:-20].active = YES;
        [row.bottomAnchor constraintEqualToAnchor:v.bottomAnchor constant:-20].active = YES;
        floor = row.topAnchor; gap = -12;
    }
    [top.topAnchor constraintGreaterThanOrEqualToAnchor:v.topAnchor constant:20].active = YES;
    [top.bottomAnchor constraintLessThanOrEqualToAnchor:floor constant:gap].active = YES;
    if (fill) {
        top.distribution = NSStackViewDistributionFill;
        [fill setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationVertical];
        [NSLayoutConstraint activateConstraints:@[
            [top.topAnchor constraintEqualToAnchor:v.topAnchor constant:20], [top.bottomAnchor constraintEqualToAnchor:floor constant:gap],
            [top.leadingAnchor constraintEqualToAnchor:v.leadingAnchor constant:20], [top.trailingAnchor constraintEqualToAnchor:v.trailingAnchor constant:-20],
        ]];
    } else {
        [top.widthAnchor constraintEqualToConstant:440].active = YES;   /* the window is at least 600 wide */
        [top.centerXAnchor constraintEqualToAnchor:v.centerXAnchor].active = YES;
        NSLayoutConstraint *middle = [top.centerYAnchor constraintEqualToAnchor:v.centerYAnchor];
        middle.priority = NSLayoutPriorityDefaultHigh; middle.active = YES;
    }
    for (NSView *x in views) if (![x isKindOfClass:NSTextField.class] || ((NSTextField *)x).isEditable) [x.trailingAnchor constraintEqualToAnchor:top.trailingAnchor].active = YES;
    return v;
}
- (NSScrollView *)scrolling:(NSTableView *)t columns:(NSArray *)columns {
    for (NSArray *c in columns) {
        NSTableColumn *col = [[NSTableColumn alloc] initWithIdentifier:c[0]]; col.title = c[1]; col.width = [c[2] doubleValue]; [t addTableColumn:col];
    }
    t.dataSource = self; t.delegate = self; t.usesAlternatingRowBackgroundColors = YES; t.allowsEmptySelection = YES;
    t.style = NSTableViewStyleFullWidth;   /* the inset style's rounded empty rows read as placeholders */
    NSScrollView *scroll = [[NSScrollView alloc] init];
    scroll.documentView = t; scroll.hasVerticalScroller = YES;
    [scroll.heightAnchor constraintGreaterThanOrEqualToConstant:110].active = YES;
    return scroll;
}
- (void)show:(NSView *)screen title:(NSString *)title {
    self.window.contentView = screen; self.window.title = [@"Super Smash Bros. Ultimate — " stringByAppendingString:title];
}
- (NSInteger)run {
    NSString *last = nil;
    self.servers = load_servers(self.path, &last); self.last = last;
    self.status = [NSMutableDictionary dictionary]; self.choice = CHOICE_QUIT; self.row = -1;
    NSWindow *w = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 660, 460)
                                              styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable
                                                backing:NSBackingStoreBuffered defer:NO];
    w.delegate = self; w.releasedWhenClosed = NO; w.minSize = NSMakeSize(600, 440);
    self.window = w;
    [self showStart:nil];
    [w center]; [w makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
    [NSApp runModalForWindow:w];
    [w orderOut:nil];
    return self.choice;
}

/* ---- 1. Start ---- */
- (void)showStart:(id)sender {
    (void)sender;
    NSButton *offline = [self button:@"Play Offline" action:@selector(playOffline:)];
    NSButton *online = [self button:@"Play on a Server" action:@selector(showServers:)];
    for (NSButton *b in @[offline, online]) { b.controlSize = NSControlSizeLarge; b.font = [NSFont systemFontOfSize:17]; [b.heightAnchor constraintEqualToConstant:44].active = YES; }
    online.keyEquivalent = @"\r";
    NSTextField *note = [self label:@"Offline, every online feature is off. On a server (ssbu-oof), you sign in with that server's account; "
                                     "friends and requests are on the next screens." size:12];
    NSView *v = [self screen:@[[self label:@"Super Smash Bros. Ultimate" size:22], note, offline, online]
                     buttons:@[[self button:@"Add DLC…" action:@selector(addDLC:)]] fill:nil];
    [self show:v title:@"Start"];
}
- (void)playOffline:(id)sender { (void)sender; [self finish:CHOICE_OFFLINE]; }

/* ---- 2. Servers ---- */
- (void)showServers:(id)sender {
    (void)sender;
    self.table = [[NSTableView alloc] init];
    NSScrollView *scroll = [self scrolling:self.table columns:@[@[@"name", @"Server", @180], @[@"address", @"Address", @200], @[@"status", @"Status", @200]]];
    self.table.target = self; self.table.doubleAction = @selector(chooseServer:);
    [self.table registerForDraggedTypes:@[NSPasteboardTypeFileURL]];
    self.note = [self label:@"" size:12];
    self.choose = [self button:@"Continue" action:@selector(chooseServer:)]; self.choose.keyEquivalent = @"\r";
    self.edit = [self button:@"Edit…" action:@selector(editServer:)];
    self.remove = [self button:@"Delete" action:@selector(removeServer:)];
    NSView *v = [self screen:@[scroll, self.note] buttons:@[[self button:@"Back" action:@selector(showStart:)],
                 [self button:@"Add…" action:@selector(addServer:)], self.edit, self.remove, self.choose] fill:scroll];
    [self show:v title:@"Servers"];
    [self.table reloadData];
    NSUInteger at = [self.servers indexOfObjectPassingTest:^BOOL(NSMutableDictionary *s, NSUInteger i, BOOL *stop) { (void)i; (void)stop; return [s[@"address"] isEqual:self.last]; }];
    if (at != NSNotFound) [self.table selectRowIndexes:[NSIndexSet indexSetWithIndex:at] byExtendingSelection:NO];
    [self updateButtons];
    [self refresh:nil];
}
- (void)updateButtons {
    BOOL selected = self.table.selectedRow >= 0;
    self.choose.enabled = selected; self.edit.enabled = selected; self.remove.enabled = selected;
    self.note.stringValue = self.servers.count ? @"Choose a server to sign in or create an account on it."
                                               : @"No servers yet: add an ssbu-oof server with its address (host:port).";
}
- (void)tableViewSelectionDidChange:(NSNotification *)n { if (n.object == self.table) [self updateButtons]; }
/* A remembered account goes straight to the signed-in screen once its device key still buys a token. */
- (void)chooseServer:(id)sender {
    (void)sender;
    NSInteger row = self.table.selectedRow; if (row < 0) return;
    self.row = row; self.last = self.servers[(NSUInteger)row][@"address"];
    if (self.servers[(NSUInteger)row][@"device_key"]) [self checkToken:^{ [self showHome]; }];
    else [self showAccount:nil];
}

/* ---- 3. Sign in / new account: a user name (also the in-game nickname) and a password; no e-mail, no phone ---- */
- (void)showAccount:(NSString *)message {
    NSMutableDictionary *s = self.servers[(NSUInteger)self.row];
    self.accountTitle = [self label:[NSString stringWithFormat:@"Sign in to %@", s[@"name"]] size:18];
    self.accountNote = [self label:message ?: @"Your user name is also your name in online matches (3-10 letters, digits, _ - or .). "
                                              "The password is never stored on this Mac." size:12];
    self.userField = [NSTextField textFieldWithString:s[@"username"] ?: @""]; self.userField.placeholderString = @"User name";
    self.passwordField = [[NSSecureTextField alloc] init]; self.passwordField.placeholderString = @"Password (8 or more characters)";
    self.rememberBox = [NSButton checkboxWithTitle:@"Remember me (sign in by itself next time)" target:nil action:nil];
    self.rememberBox.state = [s[@"remember"] boolValue] || !s[@"username"] ? NSControlStateValueOn : NSControlStateValueOff;
    self.signInButton = [self button:@"Sign In" action:@selector(signIn:)]; self.signInButton.keyEquivalent = @"\r";
    self.createButton = [self button:@"Create Account" action:@selector(signIn:)];
    NSView *v = [self screen:@[self.accountTitle, self.accountNote, self.userField, self.passwordField, self.rememberBox]
                     buttons:@[[self button:@"Back" action:@selector(showServers:)], self.createButton, self.signInButton] fill:nil];
    [self show:v title:@"Account"];
    [self.window makeFirstResponder:self.userField.stringValue.length ? self.passwordField : self.userField];
}
- (void)signIn:(id)sender {
    NSMutableDictionary *s = self.servers[(NSUInteger)self.row];
    NSString *user = [self.userField.stringValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    NSString *pw = self.passwordField.stringValue;
    if (!valid_username(user)) { [self alert:@"Invalid user name" text:@"Use 3 to 10 letters, digits, _ - or ."]; return; }
    if (pw.length < 8) { [self alert:@"Password too short" text:@"Use at least 8 characters."]; return; }
    BOOL remember = self.rememberBox.state == NSControlStateValueOn;
    NSDictionary *credentials = @{@"username": user, @"password": pw, @"device": NSHost.currentHost.localizedName ?: @"Mac"};
    self.signInButton.enabled = self.createButton.enabled = NO;
    void (^logIn)(void) = ^{
        self.accountNote.stringValue = [NSString stringWithFormat:@"Signing in to %@…", s[@"name"]];
        [self post:s api:@"/v1/login" body:credentials then:^(NSInteger code, NSDictionary *j, NSError *error) {
            self.signInButton.enabled = self.createButton.enabled = YES;
            if (code == 403) { [self showBan:j]; return; }
            if (code != 200 || !valid_account(j[@"pid"], j[@"device_key"]) || !valid_username(j[@"username"])) {
                self.accountNote.stringValue = [@"Could not sign in: " stringByAppendingString:[self serverError:j error:error]]; return;
            }
            s[@"pid"] = j[@"pid"]; s[@"device_key"] = j[@"device_key"]; s[@"username"] = j[@"username"]; s[@"remember"] = @(remember);
            if (!save_servers(self.path, self.servers, self.last)) [self alert:@"Could not save the server list" text:self.path];
            [self showHome];
        }];
    };
    if (sender == self.signInButton) { logIn(); return; }
    self.accountNote.stringValue = [NSString stringWithFormat:@"Creating your account on %@…", s[@"name"]];
    [self post:s api:@"/v1/accounts" body:credentials then:^(NSInteger code, NSDictionary *j, NSError *error) {
        if (code != 201) {
            self.signInButton.enabled = self.createButton.enabled = YES;
            self.accountNote.stringValue = [@"Could not create the account: " stringByAppendingString:[self serverError:j error:error]]; return;
        }
        logIn();
    }];
}

/* ---- 4. Signed in: Play, Friends (ssbu-oof standard v0.2 section 7), Requests ---- */
- (void)showHome {
    NSDictionary *s = self.servers[(NSUInteger)self.row];
    self.homeTitle = [self label:[NSString stringWithFormat:@"%@ on %@", s[@"username"], s[@"name"]] size:18];
    NSTabView *tabs = [[NSTabView alloc] init]; tabs.delegate = self;

    self.startButton = [self button:@"Start Game" action:@selector(startGame:)];
    self.startButton.controlSize = NSControlSizeLarge; self.startButton.font = [NSFont boldSystemFontOfSize:20]; self.startButton.keyEquivalent = @"\r";
    [self.startButton.heightAnchor constraintEqualToConstant:56].active = YES; [self.startButton.widthAnchor constraintEqualToConstant:260].active = YES;
    NSButton *signOut = [self button:@"Sign Out" action:@selector(signOut:)];
    self.homeNote = [self label:@"" size:12];
    NSStackView *stack = [NSStackView stackViewWithViews:@[self.startButton, signOut, self.homeNote]];
    stack.orientation = NSUserInterfaceLayoutOrientationVertical; stack.spacing = 14; stack.translatesAutoresizingMaskIntoConstraints = NO;
    NSView *play = [[NSView alloc] init]; [play addSubview:stack];   /* in the middle of the page, however tall */
    [stack.centerXAnchor constraintEqualToAnchor:play.centerXAnchor].active = YES; [stack.centerYAnchor constraintEqualToAnchor:play.centerYAnchor].active = YES;

    self.friendsTable = [[NSTableView alloc] init]; self.friendsNote = [self label:@"Loading…" size:12];
    NSView *friends = [self tab:@[[self scrolling:self.friendsTable columns:@[@[@"name", @"User name", @180], @[@"state", @"Status", @240]]], self.friendsNote]
                             buttons:@[[self button:@"Add Friend…" action:@selector(addFriend:)], [self button:@"Remove" action:@selector(removeFriend:)],
                                       [self button:@"Block" action:@selector(blockFriend:)], [self button:@"Unblock" action:@selector(unblockFriend:)]]];
    self.requestsTable = [[NSTableView alloc] init]; self.requestsNote = [self label:@"" size:12];
    NSView *requests = [self tab:@[[self scrolling:self.requestsTable columns:@[@[@"name", @"User name", @180], @[@"state", @"Status", @240]]], self.requestsNote]
                              buttons:@[[self button:@"Accept" action:@selector(acceptFriend:)], [self button:@"Decline" action:@selector(declineFriend:)]]];
    for (NSArray *t in @[@[@"Play", play], @[@"Friends", friends], @[@"Requests", requests]]) {
        NSTabViewItem *item = [[NSTabViewItem alloc] initWithIdentifier:t[0]]; item.label = t[0]; item.view = t[1]; [tabs addTabViewItem:item];
    }
    NSView *v = [self screen:@[self.homeTitle, tabs] buttons:@[] fill:tabs];
    [self show:v title:s[@"username"]];
    [self reloadFriends];
}
/* A tab's page: a list filling the page, a note, then its buttons; pinned to the page's edges. */
- (NSView *)tab:(NSArray<NSView *> *)views buttons:(NSArray<NSView *> *)buttons {
    NSStackView *row = [NSStackView stackViewWithViews:buttons]; row.spacing = 8;
    NSStackView *stack = [NSStackView stackViewWithViews:[views arrayByAddingObject:row]];
    stack.orientation = NSUserInterfaceLayoutOrientationVertical; stack.alignment = NSLayoutAttributeLeading; stack.spacing = 8;
    stack.distribution = NSStackViewDistributionFill; [views[0] setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationVertical];
    stack.translatesAutoresizingMaskIntoConstraints = NO;
    NSView *page = [[NSView alloc] init]; [page addSubview:stack];
    [NSLayoutConstraint activateConstraints:@[
        [stack.topAnchor constraintEqualToAnchor:page.topAnchor constant:12], [stack.bottomAnchor constraintEqualToAnchor:page.bottomAnchor constant:-12],
        [stack.leadingAnchor constraintEqualToAnchor:page.leadingAnchor constant:12], [stack.trailingAnchor constraintEqualToAnchor:page.trailingAnchor constant:-12],
        [views[0].trailingAnchor constraintEqualToAnchor:stack.trailingAnchor],
    ]];
    return page;
}
/* Before the game starts the device key must still buy a token: a revoked key asks to sign in again, a ban says why. */
- (void)checkToken:(void (^)(void))then {
    NSMutableDictionary *s = self.servers[(NSUInteger)self.row];
    self.startButton.enabled = NO; self.homeNote.stringValue = [NSString stringWithFormat:@"Connecting to %@ as %@…", s[@"name"], s[@"username"]];
    [self post:s api:@"/v1/token" body:@{@"pid": s[@"pid"], @"device_key": s[@"device_key"]} then:^(NSInteger code, NSDictionary *j, NSError *error) {
        self.startButton.enabled = YES; self.homeNote.stringValue = @"";
        if (code == 200) { then(); return; }
        if (code == 403) { [self showBan:j]; return; }
        if (code == 401) {
            [s removeObjectsForKeys:@[@"pid", @"device_key", @"remember"]]; save_servers(self.path, self.servers, self.last);
            [self showAccount:@"This Mac was signed out of the account (for example after a password change). Sign in again."]; return;
        }
        [self alert:@"Could not connect" text:[self serverError:j error:error]];
    }];
}
- (void)startGame:(id)sender {
    (void)sender;
    [self checkToken:^{ save_servers(self.path, self.servers, self.last); [self finish:self.row]; }];
}
/* Signing out forgets the device on the server and here ("Remember me" included), then back to the server list. */
- (void)signOut:(id)sender {
    (void)sender;
    NSMutableDictionary *s = self.servers[(NSUInteger)self.row];
    [self post:s api:@"/v1/logout" body:@{@"pid": s[@"pid"], @"device_key": s[@"device_key"]} then:^(NSInteger code, NSDictionary *j, NSError *error) {
        (void)code; (void)j; (void)error;   /* a server that cannot be reached still loses the key on this Mac */
    }];
    [s removeObjectsForKeys:@[@"pid", @"device_key", @"remember"]];
    if (!save_servers(self.path, self.servers, self.last)) [self alert:@"Could not save the server list" text:self.path];
    [self showServers:nil];
}
- (void)tabView:(NSTabView *)t didSelectTabViewItem:(NSTabViewItem *)item { (void)t; if (![item.identifier isEqual:@"Play"]) [self reloadFriends]; }
- (NSDictionary *)friendsBody:(NSString *)user {
    NSDictionary *s = self.servers[(NSUInteger)self.row];
    NSMutableDictionary *b = [@{@"pid": s[@"pid"], @"device_key": s[@"device_key"]} mutableCopy];
    if (user) b[@"user"] = user;
    return b;
}
- (void)reloadFriends {
    [self post:self.servers[(NSUInteger)self.row] api:@"/v1/friends" body:[self friendsBody:nil] then:^(NSInteger code, NSDictionary *j, NSError *error) {
        if (code != 200) { self.friendsNote.stringValue = [self serverError:j error:error]; return; }
        NSMutableArray *friends = [NSMutableArray array], *requests = [NSMutableArray array];
        for (NSString *kind in @[@"incoming", @"friends", @"outgoing", @"blocked"]) {
            NSArray *list = [j[kind] isKindOfClass:NSArray.class] ? j[kind] : @[];
            for (NSDictionary *f in list) {
                if (![f isKindOfClass:NSDictionary.class] || ![f[@"pid"] isKindOfClass:NSNumber.class] || ![f[@"username"] isKindOfClass:NSString.class]) continue;
                NSDictionary *row = @{@"kind": [kind isEqual:@"friends"] ? @"friend" : kind, @"pid": f[@"pid"], @"username": f[@"username"],
                                      @"online": @([f[@"online"] isKindOfClass:NSNumber.class] && [f[@"online"] boolValue])};
                [([kind isEqual:@"incoming"] || [kind isEqual:@"outgoing"] ? requests : friends) addObject:row];
            }
        }
        self.friendRows = friends; self.requestRows = requests;
        [self.friendsTable reloadData]; [self.requestsTable reloadData];
        NSUInteger count = [[friends filteredArrayUsingPredicate:[NSPredicate predicateWithFormat:@"kind == 'friend'"]] count];
        NSUInteger incoming = [[requests filteredArrayUsingPredicate:[NSPredicate predicateWithFormat:@"kind == 'incoming'"]] count];
        self.friendsNote.stringValue = [NSString stringWithFormat:@"%lu of %@ friends. Friends see each other's Battle Arenas in the game "
                                        "(Battle Arenas › Search › Friends' Arenas).", (unsigned long)count, j[@"max"] ?: @300];
        self.requestsNote.stringValue = incoming ? [NSString stringWithFormat:@"%lu waiting for your answer.", (unsigned long)incoming] : @"No requests waiting.";
    }];
}
/* One change (add / remove / block / unblock) on the selected row or a typed user name, then the lists again. */
- (void)friendCall:(NSString *)api user:(NSString *)user {
    if (!user.length) return;
    [self post:self.servers[(NSUInteger)self.row] api:api body:[self friendsBody:user] then:^(NSInteger code, NSDictionary *j, NSError *error) {
        if (code != 200 && code != 204) [self alert:@"Friends" text:[self serverError:j error:error]];
        else if ([j[@"state"] isEqual:@"requested"]) self.friendsNote.stringValue = [NSString stringWithFormat:@"Request sent to %@.", user];
        [self reloadFriends];
    }];
}
- (NSString *)selected:(NSTableView *)t rows:(NSArray *)rows kinds:(NSArray *)kinds {
    NSInteger row = t.selectedRow;
    if (row < 0) { [self alert:@"Friends" text:@"Select someone in the list first."]; return nil; }
    NSDictionary *f = rows[(NSUInteger)row];
    if (![kinds containsObject:f[@"kind"]]) { [self alert:@"Friends" text:@"That does not apply to the selected row."]; return nil; }
    return [f[@"pid"] stringValue];
}
- (void)addFriend:(id)sender {
    (void)sender;
    NSAlert *a = [[NSAlert alloc] init];
    a.messageText = @"Add a friend"; a.informativeText = @"Their user name on this server. They see your request under Requests and accept it.";
    [a addButtonWithTitle:@"Send Request"]; [a addButtonWithTitle:@"Cancel"];
    NSTextField *name = [NSTextField textFieldWithString:@""]; name.placeholderString = @"User name";
    name.frame = NSMakeRect(0, 0, 260, 24); a.accessoryView = name; a.window.initialFirstResponder = name;
    if ([a runModal] != NSAlertFirstButtonReturn) return;
    [self friendCall:@"/v1/friends/add" user:[name.stringValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet]];
}
- (void)acceptFriend:(id)sender { (void)sender; [self friendCall:@"/v1/friends/add" user:[self selected:self.requestsTable rows:self.requestRows kinds:@[@"incoming"]]]; }
- (void)declineFriend:(id)sender { (void)sender; [self friendCall:@"/v1/friends/remove" user:[self selected:self.requestsTable rows:self.requestRows kinds:@[@"incoming", @"outgoing"]]]; }
- (void)removeFriend:(id)sender { (void)sender; [self friendCall:@"/v1/friends/remove" user:[self selected:self.friendsTable rows:self.friendRows kinds:@[@"friend"]]]; }
- (void)blockFriend:(id)sender { (void)sender; [self friendCall:@"/v1/friends/block" user:[self selected:self.friendsTable rows:self.friendRows kinds:@[@"friend"]]]; }
- (void)unblockFriend:(id)sender { (void)sender; [self friendCall:@"/v1/friends/unblock" user:[self selected:self.friendsTable rows:self.friendRows kinds:@[@"blocked"]]]; }
- (NSInteger)numberOfRowsInTableView:(NSTableView *)t {
    return (NSInteger)(t == self.friendsTable ? self.friendRows.count : t == self.requestsTable ? self.requestRows.count : self.servers.count);
}
- (id)tableView:(NSTableView *)t objectValueForTableColumn:(NSTableColumn *)col row:(NSInteger)row {
    if (t == self.friendsTable || t == self.requestsTable) {
        NSDictionary *f = (t == self.friendsTable ? self.friendRows : self.requestRows)[(NSUInteger)row];
        if ([col.identifier isEqual:@"name"]) return f[@"username"];
        NSString *kind = f[@"kind"];
        if ([kind isEqual:@"friend"]) return [f[@"online"] boolValue] ? @"Online" : @"Offline";
        return [kind isEqual:@"incoming"] ? @"Wants to be your friend" : [kind isEqual:@"outgoing"] ? @"Request sent" : @"Blocked";
    }
    NSDictionary *s = self.servers[(NSUInteger)row];
    if ([col.identifier isEqual:@"status"]) return self.status[s[@"address"]] ?: @"…";
    if ([col.identifier isEqual:@"address"]) return [s[@"tls"] boolValue] ? s[@"address"] : [s[@"address"] stringByAppendingString:@" (no TLS)"];
    return s[@"name"];
}
- (BOOL)tableView:(NSTableView *)t shouldEditTableColumn:(NSTableColumn *)col row:(NSInteger)row { (void)t; (void)col; (void)row; return NO; }
- (void)alert:(NSString *)title text:(NSString *)text {
    NSAlert *a = [[NSAlert alloc] init]; a.messageText = title; a.informativeText = text; [a runModal];
}
/* GET /v1/status (STANDARD.md 8): name, players online and round trip, like a game server list. */
- (void)refresh:(id)sender {
    (void)sender;
    for (NSDictionary *s in [self.servers copy]) {
        NSString *address = s[@"address"]; self.status[address] = @"Checking…";
        NSMutableURLRequest *r = [NSMutableURLRequest requestWithURL:server_url(s, @"/v1/status")];
        r.timeoutInterval = 5; r.cachePolicy = NSURLRequestReloadIgnoringLocalCacheData;
        CFAbsoluteTime start = CFAbsoluteTimeGetCurrent();
        server_request(s, r, ^(NSData *data, NSURLResponse *response, NSError *error) {
            double ms = (CFAbsoluteTimeGetCurrent() - start) * 1000;
            NSDictionary *j = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
            NSString *line;
            if (error) line = [NSString stringWithFormat:@"Unreachable (%@)", error.localizedDescription];
            else if (((NSHTTPURLResponse *)response).statusCode != 200 || ![j isKindOfClass:NSDictionary.class] || ![j[@"server"] isEqual:@"ssbu-oof"])
                line = @"Not an ssbu-oof server";
            else line = [NSString stringWithFormat:@"Online · %@ players · %.0f ms", [j[@"players_online"] isKindOfClass:NSNumber.class] ? j[@"players_online"] : @"?", ms];
            dispatch_async(dispatch_get_main_queue(), ^{ self.status[address] = line; [self.table reloadData]; });
        });
    }
    [self.table reloadData];
}
- (void)addServer:(id)sender { (void)sender; [self serverDialog:nil]; }
- (void)editServer:(id)sender { (void)sender; if (self.table.selectedRow >= 0) [self serverDialog:self.servers[(NSUInteger)self.table.selectedRow]]; }
/* Add (entry nil) or edit a server. A new address is another server: the account saved for the old one is forgotten. */
- (void)serverDialog:(NSMutableDictionary *)entry {
    NSAlert *a = [[NSAlert alloc] init];
    a.messageText = entry ? @"Edit Server" : @"Add Server"; a.informativeText = @"The address of an ssbu-oof server, as host:port.";
    [a addButtonWithTitle:entry ? @"Save" : @"Add"]; [a addButtonWithTitle:@"Cancel"];
    NSTextField *name = [NSTextField textFieldWithString:entry[@"name"] ?: @""], *address = [NSTextField textFieldWithString:entry[@"address"] ?: @""];
    name.placeholderString = @"Server name"; address.placeholderString = @"play.example.org:443";
    NSButton *tls = [NSButton checkboxWithTitle:@"Secure connection (TLS)" target:nil action:nil];
    tls.state = !entry || [entry[@"tls"] boolValue] ? NSControlStateValueOn : NSControlStateValueOff;
    NSTextField *ca = [NSTextField textFieldWithString:entry[@"ca"] ?: @""]; ca.placeholderString = @"Server's own CA certificate (.pem), optional";
    NSButton *choose = [self button:@"Choose CA Certificate…" action:@selector(chooseCA:)]; self.caField = ca;
    NSStackView *stack = [NSStackView stackViewWithViews:@[name, address, tls, ca, choose]];
    stack.orientation = NSUserInterfaceLayoutOrientationVertical; stack.alignment = NSLayoutAttributeLeading;
    stack.frame = NSMakeRect(0, 0, 300, 146);
    for (NSTextField *f in @[name, address, ca]) [f.widthAnchor constraintEqualToConstant:300].active = YES;
    a.accessoryView = stack; a.window.initialFirstResponder = name;
    if ([a runModal] != NSAlertFirstButtonReturn) return;
    BOOL secure = tls.state == NSControlStateValueOn;
    NSString *normal = normalize_address(address.stringValue, &secure);
    NSString *label = [name.stringValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if (!normal) { [self alert:@"Invalid address" text:@"Use host:port, for example play.example.org:443 or 192.168.1.20:8080."]; return; }
    NSString *caPath = [ca.stringValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if (caPath.length && !ca_certificates(caPath)) { [self alert:@"Not a CA certificate" text:@"Pick the server's PEM certificate file (it starts with -----BEGIN CERTIFICATE-----)."]; return; }
    for (NSDictionary *s in self.servers)
        if (s != entry && [s[@"address"] isEqual:normal]) { [self alert:@"Already in the list" text:[NSString stringWithFormat:@"%@ is listed as “%@”.", normal, s[@"name"]]]; return; }
    NSMutableDictionary *e = entry ?: [NSMutableDictionary dictionary];
    if (entry && ![entry[@"address"] isEqual:normal]) [e removeObjectsForKeys:@[@"pid", @"device_key", @"remember", @"username"]];
    e[@"name"] = label.length ? label : normal; e[@"address"] = normal; e[@"tls"] = @(secure);
    if (caPath.length) e[@"ca"] = caPath; else [e removeObjectForKey:@"ca"];
    if (!entry) [self.servers addObject:e];
    if (!save_servers(self.path, self.servers, self.last)) [self alert:@"Could not save the server list" text:self.path];
    [self.table reloadData];
    [self.table selectRowIndexes:[NSIndexSet indexSetWithIndex:[self.servers indexOfObjectIdenticalTo:e]] byExtendingSelection:NO];
    [self refresh:nil];
}
- (void)chooseCA:(id)sender {
    (void)sender;
    NSOpenPanel *p = [NSOpenPanel openPanel]; p.message = @"The server's CA certificate (PEM)"; p.canChooseDirectories = NO;
    if ([p runModal] == NSModalResponseOK) self.caField.stringValue = p.URL.path;
}
- (void)removeServer:(id)sender {
    (void)sender;
    NSInteger row = self.table.selectedRow; if (row < 0) return;
    NSDictionary *s = self.servers[(NSUInteger)row];
    NSAlert *a = [[NSAlert alloc] init];
    a.messageText = [NSString stringWithFormat:@"Remove “%@”?", s[@"name"]];
    a.informativeText = s[@"pid"] ? @"This Mac forgets the account it has on that server; adding it again creates a new one." : @"";
    [a addButtonWithTitle:@"Remove"]; [a addButtonWithTitle:@"Cancel"];
    a.buttons[0].hasDestructiveAction = YES;
    if ([a runModal] != NSAlertFirstButtonReturn) return;
    [self.servers removeObjectAtIndex:(NSUInteger)row];
    if (!save_servers(self.path, self.servers, self.last)) [self alert:@"Could not save the server list" text:self.path];
    [self.table reloadData]; [self updateButtons];
}
/* stopModal takes effect on the next event; from a network completion block none may come, so post one. */
- (void)finish:(NSInteger)choice {
    self.choice = choice; [NSApp stopModal];
    [NSApp postEvent:[NSEvent otherEventWithType:NSEventTypeApplicationDefined location:NSZeroPoint modifierFlags:0 timestamp:0
                                    windowNumber:0 context:nil subtype:0 data1:0 data2:0] atStart:YES];
}
/* DLC NSPs from a panel or a drop: indices are added to dlc.json; files that are not SSBU DLC are named. */
- (void)importDLC:(NSArray<NSString *> *)paths {
    NSMutableArray *rejected = [NSMutableArray array]; NSMutableSet *have = load_dlc(self.dlcPath);
    NSUInteger before = have.count; [have unionSet:dlc_scan(paths, rejected)];
    if (have.count != before && !save_dlc(self.dlcPath, have)) { [self alert:@"Could not save the DLC list" text:self.dlcPath]; return; }
    NSString *text = [NSString stringWithFormat:@"%lu new, %lu DLC in total.", (unsigned long)(have.count - before), (unsigned long)have.count];
    if (rejected.count) text = [text stringByAppendingFormat:@"\n\nNot Super Smash Bros. Ultimate DLC (skipped):\n%@",
                                [[rejected subarrayWithRange:NSMakeRange(0, MIN(rejected.count, 20u))] componentsJoinedByString:@"\n"]];
    [self alert:@"DLC added" text:text];
}
- (void)addDLC:(id)sender {
    (void)sender; NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.canChooseFiles = YES; panel.canChooseDirectories = YES; panel.allowsMultipleSelection = YES;
    panel.message = @"Choose your Super Smash Bros. Ultimate DLC (.nsp files or a folder holding them)";
    if ([panel runModal] != NSModalResponseOK) return;
    NSMutableArray *paths = [NSMutableArray array]; for (NSURL *u in panel.URLs) [paths addObject:u.path];
    [self importDLC:paths];
}
- (NSDragOperation)tableView:(NSTableView *)t validateDrop:(id<NSDraggingInfo>)info proposedRow:(NSInteger)row proposedDropOperation:(NSTableViewDropOperation)op {
    (void)t; (void)info; (void)row; (void)op; return NSDragOperationCopy;
}
- (BOOL)tableView:(NSTableView *)t acceptDrop:(id<NSDraggingInfo>)info row:(NSInteger)row dropOperation:(NSTableViewDropOperation)op {
    (void)t; (void)row; (void)op; NSMutableArray *paths = [NSMutableArray array];
    for (NSURL *u in [info.draggingPasteboard readObjectsForClasses:@[NSURL.class] options:@{NSPasteboardURLReadingFileURLsOnlyKey: @YES}]) [paths addObject:u.path];
    [self importDLC:paths]; return paths.count > 0;
}
- (void)windowWillClose:(NSNotification *)n { (void)n; if (NSApp.modalWindow == self.window) [NSApp stopModal]; }
/* POST a JSON body to one of the server's /v1 calls; the reply comes on the main thread (status 0: no answer). */
- (void)post:(NSDictionary *)s api:(NSString *)api body:(NSDictionary *)body then:(void (^)(NSInteger, NSDictionary *, NSError *))then {
    NSMutableURLRequest *r = [NSMutableURLRequest requestWithURL:server_url(s, api)];
    r.HTTPMethod = @"POST"; r.timeoutInterval = 15; [r setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
    r.HTTPBody = [NSJSONSerialization dataWithJSONObject:body options:0 error:nil];
    server_request(s, r, ^(NSData *data, NSURLResponse *response, NSError *error) {
        NSDictionary *j = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        NSInteger code = [response isKindOfClass:NSHTTPURLResponse.class] ? ((NSHTTPURLResponse *)response).statusCode : 0;
        dispatch_async(dispatch_get_main_queue(), ^{ then(code, [j isKindOfClass:NSDictionary.class] ? j : @{}, error); });
    });
}
- (NSString *)serverError:(NSDictionary *)j error:(NSError *)error {
    if (error) return error.localizedDescription;
    return [j[@"error"] isKindOfClass:NSString.class] ? j[@"error"] : @"The server's answer is not an ssbu-oof reply.";
}
- (void)showBan:(NSDictionary *)j {
    NSString *reason = [j[@"reason"] isKindOfClass:NSString.class] ? j[@"reason"] : @"";
    NSString *until = [j[@"until"] isKindOfClass:NSNumber.class]
        ? [NSDateFormatter localizedStringFromDate:[NSDate dateWithTimeIntervalSince1970:[j[@"until"] doubleValue]]
                                         dateStyle:NSDateFormatterMediumStyle timeStyle:NSDateFormatterShortStyle]
        : @"permanently";
    [self alert:@"This account is banned on this server" text:[NSString stringWithFormat:@"Until: %@\nReason: %@", until, reason]];
}
@end

int main(void) {
    char exe[PATH_MAX], real[PATH_MAX]; uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) || !realpath(exe, real)) return 1;
    char macos[PATH_MAX], contents[PATH_MAX], app[PATH_MAX], home[PATH_MAX];
    parent_of(macos, real); parent_of(contents, macos); parent_of(app, contents); parent_of(home, app);

    char game[PATH_MAX], runtime[PATH_MAX], modules[PATH_MAX], romfs[PATH_MAX], fonts[PATH_MAX], resources[PATH_MAX];
    join(resources, contents, "Resources"); join(game, resources, "game");
    join(runtime, macos, "ssbu-native"); join(modules, game, "rewritten/local");
    join(romfs, game, "romfs"); join(fonts, game, "fonts");

    char data[PATH_MAX], profile[PATH_MAX], cache[PATH_MAX], commands[PATH_MAX], log[PATH_MAX], servers[PATH_MAX];
    join(data, home, "SSBU Data"); join(profile, data, "profile");
    join(cache, data, "shader-cache/" SHADER_CHAIN);
    join(commands, data, "commands.txt"); join(log, data, "last-run.log");
    join(servers, data, "servers.json");
    char dlc_path[PATH_MAX]; join(dlc_path, data, "dlc.json");
    mkdir(data, 0755); mkdir(profile, 0700);

    /* The server list comes first; closing it quits. Then the launcher leaves the Dock to the game's own window. */
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
        /* Key equivalents come from the main menu: Quit, and Edit so an address can be pasted into Add Server. */
        NSMenu *bar = [[NSMenu alloc] init], *appMenu = [[NSMenu alloc] init], *edit = [[NSMenu alloc] initWithTitle:@"Edit"];
        [appMenu addItemWithTitle:@"Quit SSBU" action:@selector(terminate:) keyEquivalent:@"q"];
        for (NSArray *e in @[@[@"Undo", @"undo:", @"z"], @[@"Cut", @"cut:", @"x"], @[@"Copy", @"copy:", @"c"], @[@"Paste", @"paste:", @"v"], @[@"Select All", @"selectAll:", @"a"]])
            [edit addItemWithTitle:e[0] action:NSSelectorFromString(e[1]) keyEquivalent:e[2]];
        [bar addItemWithTitle:@"" action:nil keyEquivalent:@""].submenu = appMenu;
        [bar addItemWithTitle:@"Edit" action:nil keyEquivalent:@""].submenu = edit;
        NSApp.mainMenu = bar;
        [NSApp finishLaunching];   /* registers the app (accessibility, activation) without running its event loop */
        ServerPicker *picker = [[ServerPicker alloc] init];
        picker.path = @(servers); picker.dlcPath = @(dlc_path);
        NSInteger choice = [picker run];
        if (choice == CHOICE_QUIT) return 0;
        for (const char *const *v = (const char *const[]){"OOF_SERVER", "OOF_TLS", "OOF_PID", "OOF_DEVICE_KEY", "OOF_USERNAME", "OOF_CA", NULL}; *v; v++) unsetenv(*v);
        if (choice >= 0) {
            NSDictionary *s = picker.servers[(NSUInteger)choice];
            setenv("OOF_SERVER", [s[@"address"] UTF8String], 1);
            setenv("OOF_TLS", [s[@"tls"] boolValue] ? "1" : "0", 1);
            setenv("OOF_PID", [[s[@"pid"] stringValue] UTF8String], 1);
            setenv("OOF_DEVICE_KEY", [s[@"device_key"] UTF8String], 1);
            setenv("OOF_USERNAME", [s[@"username"] UTF8String], 1);
            if ([s[@"ca"] length]) setenv("OOF_CA", [s[@"ca"] UTF8String], 1);
        }
        NSArray *owned = [load_dlc(@(dlc_path)).allObjects sortedArrayUsingSelector:@selector(compare:)];
        if (owned.count) setenv("SSBU_AOC", [owned componentsJoinedByString:@","].UTF8String, 1); else unsetenv("SSBU_AOC");
        [NSApp setActivationPolicy:NSApplicationActivationPolicyProhibited];
    }

    /* The project's slot directory when the app was packaged there (so test runs and the app never overlap),
       otherwise one next to the data. */
    char slot[PATH_MAX] = "", slot_file[PATH_MAX]; join(slot_file, game, "slot-dir.txt");
    FILE *sf = fopen(slot_file, "r");
    if (sf) { if (fgets(slot, sizeof slot, sf)) slot[strcspn(slot, "\n")] = 0; fclose(sf); }
    struct stat st;
    if (!*slot || stat(slot, &st) || !S_ISDIR(st.st_mode)) { join(slot, data, "game-slot"); mkdir(slot, 0755); }

    FILE *cf = fopen(commands, "w"); if (cf) fclose(cf);
    setenv("TOTK_GPU_ALLOW_SUBMIT", "1", 1); setenv("TOTK_GPU_MIDCLEAR", "1", 1);
    setenv("TOTK_PROFILE_DIR", profile, 1); setenv("TOTK_SHADER_CACHE", cache, 1);
    setenv("TOTK_ROMFS", romfs, 1); setenv("TOTK_SHARED_FONTS", fonts, 1); setenv("TOTK_INPUT_FILE", commands, 1);
    setenv("TOTK_GAME_SLOT_DIR", slot, 1); setenv("TOTK_SLOT_OWNER", "user", 1); setenv("TOTK_SLOT_PURPOSE", "SSBU.app", 1);
    /* Full screen unless "SSBU Data/windowed" exists; ⌃⌘F or the green button leaves it. */
    char windowed[PATH_MAX]; join(windowed, data, "windowed");
    if (stat(windowed, &st)) setenv("BY3_FULLSCREEN", "1", 0);
    for (const char *const *v = (const char *const[]){"TOTK_ASSUME_FOCUS", "TOTK_VERIFY_STARTUP", "TOTK_HEADLESS", NULL}; *v; v++) unsetenv(*v);
    if (chdir(data)) return 1;
    char stamp[32]; time_t launched = time(NULL); strftime(stamp, sizeof stamp, "%Y%m%d-%H%M%S", localtime(&launched));
    back_up_saves(profile, data, stamp);

    pid_t child = fork();
    if (child < 0) return 1;
    if (child == 0) {
        char previous[PATH_MAX]; join(previous, data, "previous-run.log");
        rename(log, previous);   /* keep the last session's log: a crash is often reported after relaunching */
        int fd = open(log, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd >= 0) { dup2(fd, 1); dup2(fd, 2); close(fd); }
        execl(runtime, "ssbu-native", modules, "boot", (char *)NULL);
        perror("SSBU: could not start the game"); _exit(127);
    }
    int status;
    while (waitpid(child, &status, 0) < 0) if (errno != EINTR) return 1;
    int code = WIFEXITED(status) ? WEXITSTATUS(status) : 1;
    if (code) {   /* a crash: keep its log apart from the next session's */
        char crash[PATH_MAX], name[64]; snprintf(name, sizeof name, "crash-%s.log", stamp);
        join(crash, data, name); rename(log, crash);
    }
    return code;
}
