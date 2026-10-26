/* The launcher's server list (scripts/ssbu_app_launcher.m): address parsing, account validation, servers.json. No UI. */
#define main launcher_main
#include "../../scripts/ssbu_app_launcher.m"
#undef main
#include <assert.h>

static NSString *norm(NSString *s, BOOL tls_in, BOOL *tls_out) { BOOL t = tls_in; NSString *r = normalize_address(s, &t); *tls_out = t; return r; }
/* A minimal NSP: PFS0 with one entry named <name> (the parser reads only the header and the names). */
static NSString *nsp(NSString *dir, NSString *file, const char *name) {
    NSMutableData *d = [NSMutableData data]; uint32_t files = 1, strings = (uint32_t)strlen(name) + 1, zero = 0;
    [d appendBytes:"PFS0" length:4]; [d appendBytes:&files length:4]; [d appendBytes:&strings length:4]; [d appendBytes:&zero length:4];
    uint64_t off = 0, size = 0; [d appendBytes:&off length:8]; [d appendBytes:&size length:8]; [d appendBytes:&zero length:4]; [d appendBytes:&zero length:4];
    [d appendBytes:name length:strings];
    NSString *path = [dir stringByAppendingPathComponent:file]; [d writeToFile:path atomically:YES]; return path;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        assert(argc == 2);
        BOOL tls;
        assert([norm(@" Play.Example.org:8090 ", NO, &tls) isEqual:@"play.example.org:8090"] && !tls);
        assert([norm(@"play.example.org", YES, &tls) isEqual:@"play.example.org:443"] && tls);
        assert([norm(@"play.example.org", NO, &tls) isEqual:@"play.example.org:80"] && !tls);
        assert([norm(@"wss://play.example.org/", NO, &tls) isEqual:@"play.example.org:443"] && tls);
        assert([norm(@"http://127.0.0.1:8080", YES, &tls) isEqual:@"127.0.0.1:8080"] && !tls);
        for (NSString *bad in @[@"", @":80", @"host:", @"host:0", @"host:65536", @"host:12a", @"a:b:c", @"bad host:80", @"h/x:80", @"[::1]:80"])
            assert(!norm(bad, NO, &tls));
        assert(valid_account(@123, @"00112233445566778899aabbccddeeff"));
        assert(!valid_account(@0, @"00112233445566778899aabbccddeeff"));
        assert(!valid_account(@(1ULL << 53), @"00112233445566778899aabbccddeeff"));
        assert(!valid_account(@1.5, @"00112233445566778899aabbccddeeff"));
        assert(!valid_account(@YES, @"00112233445566778899aabbccddeeff"));
        assert(!valid_account(@"123", @"00112233445566778899aabbccddeeff"));
        assert(!valid_account(@123, @"not hex at all, not hex at all"));
        NSString *path = [@(argv[1]) stringByAppendingPathComponent:@"servers.json"];
        assert(valid_username(@"Ana.1") && !valid_username(@"ab") && !valid_username(@"elevenchars") && !valid_username(@"a b") && !valid_username(@7));
        NSString *hand = @"{\"last\":\"a.example:1\",\"servers\":[{\"name\":\" A \",\"address\":\"a.example:1\",\"tls\":false,\"pid\":7,\"device_key\":\"0011223344556677\",\"username\":\"Ana.1\"},"
                          "{\"name\":\"B\",\"address\":\"no good:1\"},{\"name\":\"C\",\"address\":\"c.example\",\"tls\":true,\"pid\":\"x\",\"device_key\":\"zz\",\"username\":\"Bea\"},"
                          "{\"name\":\"D\",\"address\":\"d.example:2\",\"pid\":8,\"password\":\"0011223344556677\"},42]}";
        [hand writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:nil];
        NSString *last = nil;
        NSMutableArray *list = load_servers(path, &last);
        assert([last isEqual:@"a.example:1"] && list.count == 3);   /* B (bad address) and 42 dropped */
        assert([list[0][@"name"] isEqual:@"A"] && [list[0][@"pid"] isEqual:@7] && [list[0][@"username"] isEqual:@"Ana.1"]);
        assert([list[1][@"address"] isEqual:@"c.example:443"] && !list[1][@"pid"]);   /* invalid account dropped, server kept */
        assert([list[0][@"remember"] boolValue] && [list[1][@"username"] isEqual:@"Bea"]);   /* old entries: remembered; names kept */
        assert(!list[2][@"pid"] && !list[2][@"password"]);   /* a v0.1 password account is not kept */
        assert(save_servers(path, list, last));
        struct stat st; assert(!stat(path.fileSystemRepresentation, &st) && (st.st_mode & 0777) == 0600);
        NSMutableArray *again = load_servers(path, &last);
        assert([again isEqual:list]);
        assert(load_servers([path stringByAppendingString:@".missing"], NULL).count == 0);
        /* Without "Remember me" the session stays in memory: the file keeps the user name, not the key */
        NSMutableDictionary *session = [list[0] mutableCopy]; session[@"remember"] = @NO;
        assert(save_servers(path, @[session], last));
        NSDictionary *forgot = load_servers(path, NULL)[0];
        assert([forgot[@"username"] isEqual:@"Ana.1"] && !forgot[@"pid"] && !forgot[@"device_key"] && !forgot[@"remember"]);
        /* A server's own CA: kept with the server (non-strings dropped); its certificate is the only anchor for the server */
        NSString *ca = [@(argv[1]) stringByAppendingPathComponent:@"ca.pem"];
        [[NSString stringWithFormat:@"{\"servers\":[{\"name\":\"P\",\"address\":\"localhost:8443\",\"tls\":true,\"ca\":\"%@\"},"
                                     "{\"name\":\"Q\",\"address\":\"q.example:1\",\"ca\":7}]}", ca] writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:nil];
        NSMutableArray *withCA = load_servers(path, NULL);
        assert([withCA[0][@"ca"] isEqual:ca] && !withCA[1][@"ca"]);
        NSArray *anchors = ca_certificates(ca); assert(anchors.count == 1);
        assert(!ca_certificates([@(argv[1]) stringByAppendingPathComponent:@"servers.json"]) && !ca_certificates(@"") && !ca_certificates(nil));
        SecTrustRef trust = NULL; SecPolicyRef ssl = SecPolicyCreateSSL(true, CFSTR("localhost"));
        assert(!SecTrustCreateWithCertificates((__bridge CFArrayRef)anchors, ssl, &trust));
        assert(!SecTrustEvaluateWithError(trust, NULL));   /* the system store alone does not trust it */
        assert(!trusted_by(trust, @[]) && trusted_by(trust, anchors));
        SecPolicyRef other = SecPolicyCreateSSL(true, CFSTR("play.example.org")); SecTrustSetPolicies(trust, other);
        assert(!trusted_by(trust, anchors));   /* still bound to the server's host name */
        CFRelease(other); CFRelease(ssl); CFRelease(trust);
        /* DLC: an SSBU licence (index 0x45), another game's DLC, something that is not an NSP; a folder and a file */
    NSString *dir = [@(argv[1]) stringByAppendingPathComponent:@"dlc"]; [NSFileManager.defaultManager createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:nil];
    nsp(dir, @"costume.nsp", "01006a800016f0450000000000000000.tik");
    nsp(dir, @"other.nsp", "0100000000010001000000000000000a.tik");
    [@"not an nsp" writeToFile:[dir stringByAppendingPathComponent:@"notes.txt"] atomically:YES encoding:NSUTF8StringEncoding error:nil];
    NSString *pack = nsp(@(argv[1]), @"pack.nsp", "01006A800016F0010000000000000000.tik");
    assert(dlc_index(pack) == 1 && dlc_index([dir stringByAppendingPathComponent:@"other.nsp"]) == 0);
    NSMutableArray *rejected = [NSMutableArray array];
    NSSet *found = dlc_scan(@[dir, pack], rejected);
    NSSet *expect = [NSSet setWithObjects:@0x45, @1, nil]; assert([found isEqualToSet:expect]);
    assert(rejected.count == 2 && [rejected containsObject:@"other.nsp"] && [rejected containsObject:@"notes.txt"]);
    NSString *dlc_json = [@(argv[1]) stringByAppendingPathComponent:@"dlc.json"];
    assert(save_dlc(dlc_json, found) && [load_dlc(dlc_json) isEqualToSet:found]);
    [@"{\"indices\":[1, 0, 4096, 2.5, \"3\", 70]}" writeToFile:dlc_json atomically:YES encoding:NSUTF8StringEncoding error:nil];
    NSSet *kept = [NSSet setWithObjects:@1, @70, nil]; assert([load_dlc(dlc_json) isEqualToSet:kept]);   /* out of range and wrong types dropped */
    puts("server list ok");
    }
    return 0;
}
