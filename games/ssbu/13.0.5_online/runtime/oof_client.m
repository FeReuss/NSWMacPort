/* Online play through an ssbu-oof server, following its port standard (ssbu-oof docs/STANDARD.md, sections 2 and 7).
   The launcher picks the server and keeps the account it got from that server's POST /v1/accounts; it passes them as
   OOF_SERVER (host:port), OOF_TLS (1 = https/wss), OOF_PID and OOF_DEVICE_KEY (standard v0.2 section 2: the launcher
   logged in with the player's user name and password and kept only this device key). Without them the game is
   offline. The ID token comes from a fresh POST /v1/token on every Ensure call. Nothing from the game, its keys or a
   Nintendo account is sent: only the pid and device key the server itself issued. */
#import <Foundation/Foundation.h>
#include "runtime.h"
#include <pthread.h>
#include <arpa/inet.h>
#include <errno.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static void refresh_friends(void), refresh_friends_now(int wait);
static char token[4096];
static size_t token_len;
static uint64_t token_expires_ns;

uint64_t runtime_oof_pid(void) {
    const char *p = getenv("OOF_PID");
    if (!p || *p < '0' || *p > '9') return 0;
    char *end; errno = 0;
    unsigned long long pid = strtoull(p, &end, 10);
    return errno || *end || !pid || pid >= (1ull << 53) ? 0 : pid;
}
static NSURL *server_url(NSString *path) {
    const char *server = getenv("OOF_SERVER"), *tls = getenv("OOF_TLS");
    if (!server || !*server || (tls && strcmp(tls, "0") && strcmp(tls, "1"))) return nil;
    NSString *authority = [NSString stringWithUTF8String:server];
    if (!authority || [authority rangeOfCharacterFromSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].location != NSNotFound) return nil;
    NSURLComponents *url = [NSURLComponents componentsWithString:[@"https://" stringByAppendingString:authority]];
    if (!url.host.length || !url.port || url.port.integerValue < 1 || url.port.integerValue > 65535 ||
        url.user || url.password || url.path.length || url.query || url.fragment || [authority containsString:@"%"])
        return nil;
    url.scheme = tls && !strcmp(tls, "0") ? @"http" : @"https";
    url.path = path;
    return url.URL;
}
static NSURL *token_url(void) { return server_url(@"/v1/token"); }
/* OOF_CA (standard section 1): the server's certificate is judged as the game's own TLS connection judges it. */
@interface OofTrust : NSObject <NSURLSessionDelegate>
@end
@implementation OofTrust
- (void)URLSession:(NSURLSession *)session didReceiveChallenge:(NSURLAuthenticationChallenge *)challenge
 completionHandler:(void (^)(NSURLSessionAuthChallengeDisposition, NSURLCredential *))done {
    (void)session;
    SecTrustRef trust = challenge.protectionSpace.serverTrust;
    if (![challenge.protectionSpace.authenticationMethod isEqualToString:NSURLAuthenticationMethodServerTrust] || !trust)
        done(NSURLSessionAuthChallengePerformDefaultHandling, nil);
    else if (runtime_oof_trust_ok(trust)) done(NSURLSessionAuthChallengeUseCredential, [NSURLCredential credentialForTrust:trust]);
    else done(NSURLSessionAuthChallengeCancelAuthenticationChallenge, nil);
}
@end
static NSURLSession *session(void) {
    static NSURLSession *s; static dispatch_once_t once;
    dispatch_once(&once, ^{
        const char *ca = getenv("OOF_CA");
        s = ca && *ca ? [NSURLSession sessionWithConfiguration:NSURLSessionConfiguration.ephemeralSessionConfiguration
                                                      delegate:[OofTrust new] delegateQueue:nil]
                      : NSURLSession.sharedSession;
    });
    return s;
}
/* One request to the server; blocks the calling guest thread, which gives its virtual core up meanwhile. */
static NSData *fetch(NSURLRequest *request, NSInteger *status) {
    __block NSData *body = nil; __block NSInteger code = 0;
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    int away = runtime_core_away();
    [[session() dataTaskWithRequest:request completionHandler:^(NSData *d, NSURLResponse *r, NSError *e) {
        (void)e; body = d; code = [r isKindOfClass:NSHTTPURLResponse.class] ? ((NSHTTPURLResponse *)r).statusCode : 0;
        dispatch_semaphore_signal(done);
    }] resume];
    dispatch_semaphore_wait(done, DISPATCH_TIME_FOREVER);
    runtime_core_back(&away);
    *status = code;
    return body;
}
int runtime_oof_enabled(void) {
    @autoreleasepool {
        const char *key = getenv("OOF_DEVICE_KEY");
        return runtime_oof_pid() && key && *key && [NSString stringWithUTF8String:key] && token_url();
    }
}

/* POST {server}/v1/token; 0 on success. Blocks the calling guest thread, which gives its virtual core up meanwhile. */
uint32_t runtime_oof_refresh_token(void) {
    @autoreleasepool {
    /* A failed Ensure must not leave the preceding token available to Load. */
    pthread_mutex_lock(&lock);
    token_len = 0; token_expires_ns = 0;
    pthread_mutex_unlock(&lock);
    if (!runtime_oof_enabled()) return 1;
    NSURL *url = token_url();
    if (!url) return 2;
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url
                                                           cachePolicy:NSURLRequestReloadIgnoringLocalCacheData timeoutInterval:15];
    request.HTTPMethod = @"POST";
    [request setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
    /* pid is sent as a number (below 2^53, so JSON keeps it exact) */
    request.HTTPBody = [NSJSONSerialization dataWithJSONObject:@{@"pid": @(runtime_oof_pid()), @"device_key": @(getenv("OOF_DEVICE_KEY"))}
                                                       options:0 error:NULL];
    NSInteger status = 0;
    NSData *body = fetch(request, &status);
    NSDictionary *json = body ? [NSJSONSerialization JSONObjectWithData:body options:0 error:NULL] : nil;
    NSString *value = [json isKindOfClass:NSDictionary.class] ? json[@"id_token"] : nil;
    NSNumber *expires = [json isKindOfClass:NSDictionary.class] ? json[@"expires_in"] : nil;
    if (status == 403 && [json isKindOfClass:NSDictionary.class] && [json[@"reason"] isKindOfClass:NSString.class])
        fprintf(stderr, "【红】[oof] this account is banned on the server: %s\n", [json[@"reason"] UTF8String]);
    if (status != 200 || ![value isKindOfClass:NSString.class] || ![expires isKindOfClass:NSNumber.class]) {
        fprintf(stderr, "[oof] POST /v1/token failed (HTTP %ld)\n", (long)status);
        return 3;
    }
    NSData *ascii = [value dataUsingEncoding:NSASCIIStringEncoding allowLossyConversion:NO];
    size_t n = ascii.length;
    double ttl = expires.doubleValue;
    /* One year bounds the timestamp arithmetic and rejects malformed server lifetimes. */
    if (!n || n >= sizeof token || memchr(ascii.bytes, 0, n) ||
        CFGetTypeID((__bridge CFTypeRef)expires) == CFBooleanGetTypeID() ||
        !isfinite(ttl) || ttl < 1 || ttl > 31536000 || ttl != floor(ttl)) return 4;
    pthread_mutex_lock(&lock);
    memcpy(token, ascii.bytes, n); token_len = n;
    token_expires_ns = runtime_now_ns() + (uint64_t)ttl * 1000000000ull;
    pthread_mutex_unlock(&lock);
    fprintf(stderr, "[oof] ID token received (%zu bytes, valid %llu s)\n", n, expires.unsignedLongLongValue);
    refresh_friends_now(1);   /* the friend list is ready before the game asks */
    return 0;
    }
}
/* Friends (standard v0.2 section 7): the lists from POST /v1/friends, fetched in the background, never on a game thread:
   after the first token and whenever the game reads a copy older than 15 s (it gets that copy meanwhile). */
static RuntimeOofFriend friend_cache[RUNTIME_OOF_FRIENDS_MAX];
static uint64_t blocked_cache[RUNTIME_OOF_FRIENDS_MAX];
static int friend_count, blocked_count, friends_busy;   /* lock */
static uint64_t friends_at_ns;                          /* lock */
/* The game reads its friend list once (at boot) and then follows the friends NotificationQueue: each refresh that changes
   the list notifies type 1 (read the list again), each friend coming online or going offline type 2 (that friend). */
static void notify_changes(const RuntimeOofFriend *old, int old_n, const RuntimeOofFriend *now, int n) {
    int same = old_n == n;
    for (int i = 0; same && i < n; i++) same = old[i].pid == now[i].pid;
    if (!same) { runtime_friends_notify(1, 0); return; }
    for (int i = 0; i < n; i++) if (old[i].online != now[i].online) runtime_friends_notify(2, now[i].pid);
}
/* wait: the caller is a guest thread that may block (the game's first read, at boot, gets the list; 5 s at most). */
static void refresh_friends_now(int wait) {
    pthread_mutex_lock(&lock);
    int start = runtime_oof_enabled() && !friends_busy && (!friends_at_ns || runtime_now_ns() - friends_at_ns > 15000000000ull);
    if (start) friends_busy = 1;
    pthread_mutex_unlock(&lock);
    if (!start) return;
    static dispatch_source_t timer;   /* kept for the life of the process: the list follows the server while the game runs */
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, dispatch_get_global_queue(QOS_CLASS_UTILITY, 0));
        dispatch_source_set_timer(timer, dispatch_time(DISPATCH_TIME_NOW, 16 * NSEC_PER_SEC), 16 * NSEC_PER_SEC, NSEC_PER_SEC);
        dispatch_source_set_event_handler(timer, ^{ @autoreleasepool { refresh_friends_now(0); } });
        dispatch_resume(timer);
    });
    dispatch_semaphore_t done = wait ? dispatch_semaphore_create(0) : nil;
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:server_url(@"/v1/friends")
                                                           cachePolicy:NSURLRequestReloadIgnoringLocalCacheData timeoutInterval:wait ? 5 : 15];
    request.HTTPMethod = @"POST";
    [request setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
    request.HTTPBody = [NSJSONSerialization dataWithJSONObject:@{@"pid": @(runtime_oof_pid()), @"device_key": @(getenv("OOF_DEVICE_KEY"))}
                                                       options:0 error:NULL];
    [[session() dataTaskWithRequest:request completionHandler:^(NSData *d, NSURLResponse *r, NSError *e) {
        (void)e;
        NSInteger status = [r isKindOfClass:NSHTTPURLResponse.class] ? ((NSHTTPURLResponse *)r).statusCode : 0;
        NSDictionary *json = d ? [NSJSONSerialization JSONObjectWithData:d options:0 error:NULL] : nil;
        BOOL object = [json isKindOfClass:NSDictionary.class];
        NSArray *friends = object ? json[@"friends"] : nil, *blocked = object ? json[@"blocked"] : nil;
        RuntimeOofFriend got[RUNTIME_OOF_FRIENDS_MAX]; uint64_t got_blocked[RUNTIME_OOF_FRIENDS_MAX]; int n = 0, nb = 0;
        BOOL ok = status == 200 && [friends isKindOfClass:NSArray.class] && [blocked isKindOfClass:NSArray.class];
        for (NSDictionary *f in ok ? friends : @[]) {
            if (![f isKindOfClass:NSDictionary.class] || ![f[@"pid"] isKindOfClass:NSNumber.class] || ![f[@"username"] isKindOfClass:NSString.class]) continue;
            if (n == RUNTIME_OOF_FRIENDS_MAX) break;
            RuntimeOofFriend *o = &got[n++]; memset(o, 0, sizeof *o);
            o->pid = [f[@"pid"] unsignedLongLongValue]; o->online = [f[@"online"] isKindOfClass:NSNumber.class] && [f[@"online"] boolValue];
            strlcpy(o->name, [f[@"username"] UTF8String] ?: "", sizeof o->name);
        }
        for (NSDictionary *b in ok ? blocked : @[])
            if ([b isKindOfClass:NSDictionary.class] && [b[@"pid"] isKindOfClass:NSNumber.class] && nb < RUNTIME_OOF_FRIENDS_MAX)
                got_blocked[nb++] = [b[@"pid"] unsignedLongLongValue];
        RuntimeOofFriend old[RUNTIME_OOF_FRIENDS_MAX]; int old_n = 0, first = 0;
        pthread_mutex_lock(&lock);
        if (ok) {
            first = !friends_at_ns; old_n = friend_count; memcpy(old, friend_cache, sizeof *old * (size_t)old_n);
            memcpy(friend_cache, got, sizeof *got * (size_t)n); friend_count = n;
            memcpy(blocked_cache, got_blocked, sizeof *got_blocked * (size_t)nb); blocked_count = nb;
        }
        friends_at_ns = runtime_now_ns(); friends_busy = 0;   /* a failure also waits 15 s before the next try */
        pthread_mutex_unlock(&lock);
        if (ok && !first) notify_changes(old, old_n, got, n);
        if (!ok) fprintf(stderr, "[oof] POST /v1/friends failed (HTTP %ld): the friend list stays as it was\n", (long)status);
        else { static int last = -1; if (n != last) { last = n; fprintf(stderr, "[oof] friends: %d (%d online), %d blocked\n", n, ({ int k = 0; for (int i = 0; i < n; i++) k += got[i].online; k; }), nb); } }
        if (done) dispatch_semaphore_signal(done);
    }] resume];
    if (done) { int away = runtime_core_away(); dispatch_semaphore_wait(done, DISPATCH_TIME_FOREVER); runtime_core_back(&away); }
}
static void refresh_friends(void) { refresh_friends_now(0); }
int runtime_oof_friends(RuntimeOofFriend *out, int max) {
    pthread_mutex_lock(&lock); int never = !friends_at_ns; pthread_mutex_unlock(&lock);
    @autoreleasepool { refresh_friends_now(never); }
    pthread_mutex_lock(&lock);
    int n = friend_count < max ? friend_count : max; if (n > 0) memcpy(out, friend_cache, sizeof *out * (size_t)n);
    pthread_mutex_unlock(&lock);
    return n > 0 ? n : 0;
}
int runtime_oof_blocked(uint64_t *out, int max) {
    @autoreleasepool { refresh_friends(); }
    pthread_mutex_lock(&lock);
    int n = blocked_count < max ? blocked_count : max; if (n > 0) memcpy(out, blocked_cache, sizeof *out * (size_t)n);
    pthread_mutex_unlock(&lock);
    return n > 0 ? n : 0;
}

/* The cached token's bytes (no NUL), as LoadNetworkServiceAccountIdTokenCache reports them; 0 when there is none. */
size_t runtime_oof_token(char *out, size_t cap) {
    pthread_mutex_lock(&lock);
    size_t n = runtime_now_ns() < token_expires_ns && token_len <= cap ? token_len : 0;
    if (n && out) memcpy(out, token, n);
    pthread_mutex_unlock(&lock);
    return n;
}

/* GET {server}/v1/status: the NAT check addresses the server announces for nncs1 and nncs2 (ssbu-oof STANDARD.md section 4).
   Pia refuses a NAT check whose two servers share an address (main+0xc6590). Asked once (a failure is kept too, so a
   missing announcement costs one request); 1 with two different IPv4 addresses. */
int runtime_oof_nat_check(struct in_addr *out) {
    static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
    static int known = -1; static struct in_addr cached[2];
    pthread_mutex_lock(&mu); int k = known; memcpy(out, cached, sizeof cached); pthread_mutex_unlock(&mu);
    if (k >= 0) return k;
    @autoreleasepool {
        struct in_addr got[2] = {{0}, {0}}; int ok = 0;
        NSURL *url = runtime_oof_enabled() ? server_url(@"/v1/status") : nil;
        if (url) {
            NSInteger status = 0;
            NSData *body = fetch([NSURLRequest requestWithURL:url cachePolicy:NSURLRequestReloadIgnoringLocalCacheData timeoutInterval:15], &status);
            NSDictionary *json = body ? [NSJSONSerialization JSONObjectWithData:body options:0 error:NULL] : nil;
            NSDictionary *nat = [json isKindOfClass:NSDictionary.class] ? json[@"nat_check"] : nil;
            NSString *a = [nat isKindOfClass:NSDictionary.class] ? nat[@"primary"] : nil, *b = [nat isKindOfClass:NSDictionary.class] ? nat[@"secondary"] : nil;
            ok = status == 200 && [a isKindOfClass:NSString.class] && [b isKindOfClass:NSString.class] &&
                 inet_pton(AF_INET, a.UTF8String, &got[0]) == 1 && inet_pton(AF_INET, b.UTF8String, &got[1]) == 1 &&
                 got[0].s_addr && got[1].s_addr && got[0].s_addr != got[1].s_addr;
            if (ok) fprintf(stderr, "[oof] NAT check servers %s and %s\n", a.UTF8String, b.UTF8String);
            else fprintf(stderr, "【红】[oof] the server announces no usable NAT check addresses (GET /v1/status, HTTP %ld): online battles fail at Pia's NAT check (2618-0006)\n", (long)status);
        }
        pthread_mutex_lock(&mu); known = ok; if (ok) memcpy(cached, got, sizeof got); memcpy(out, cached, sizeof cached); pthread_mutex_unlock(&mu);
        return ok;
    }
}
