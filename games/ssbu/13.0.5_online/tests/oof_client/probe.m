/* Exercise the production client with a local fake server and no guest/game data. */
#import "../../runtime/oof_client.m"
#include <assert.h>

static uint64_t now = 1000000000ull;
static int away_count;
uint64_t runtime_now_ns(void) { return now; }
/* net_host.c (linked for runtime_oof_trust_ok, the certificate check shared with the game's TLS) needs these */
_Noreturn void runtime_fail(const char *why) { fprintf(stderr, "FAIL: %s\n", why); exit(1); }
int runtime_ldn_active(void) { return 0; }
int runtime_module_offset(uint64_t pc, const char **name, uint64_t *off) { (void)pc; (void)name; (void)off; return 0; }
int runtime_core_away(void) { away_count++; return 1; }
void runtime_core_back(int *away) { assert(*away == 1); *away = 0; away_count--; }
void runtime_friends_notify(uint32_t type, uint64_t pid) { (void)type; (void)pid; }   /* friends_host.c: not linked here */

int main(int argc, const char **argv) {
    @autoreleasepool {
        if (argc == 2 && !strcmp(argv[1], "config")) {
            printf("%d %llu %s\n", runtime_oof_enabled(), (unsigned long long)runtime_oof_pid(),
                   token_url().scheme.UTF8String ?: "-");
            return 0;
        }
        if (argc == 2 && !strcmp(argv[1], "nat")) {   /* asked twice: the second answer comes from the first request */
            for (int i = 0; i < 2; i++) {
                struct in_addr nat[2]; int ok = runtime_oof_nat_check(nat);
                char a[INET_ADDRSTRLEN], b[INET_ADDRSTRLEN];
                printf("%d %s %s\n", ok, inet_ntop(AF_INET, &nat[0], a, sizeof a), inet_ntop(AF_INET, &nat[1], b, sizeof b));
            }
            assert(away_count == 0);
            return 0;
        }
        for (int i = 0; i < 2; i++) {
            unsigned result = runtime_oof_refresh_token();
            char bytes[4096]; size_t n = runtime_oof_token(bytes, sizeof bytes);
            printf("%u %zu %.*s\n", result, n, (int)n, bytes);
        }
        if (argc == 2 && !strcmp(argv[1], "expire")) {
            now += 3600000000000ull;
            assert(runtime_oof_token(NULL, 4096) == 0);
        }
        assert(away_count == 0);
    }
    return 0;
}
