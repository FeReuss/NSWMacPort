/* nn::account::GetNickname (account.c): the ssbu-oof account's user name online, "Local" otherwise. No game. */
#include "../../runtime/account.c"
#include <assert.h>

_Noreturn void runtime_fail(const char *why) { fprintf(stderr, "FAIL: %s\n", why); exit(1); }
static int online;
int runtime_oof_enabled(void) { return online; }
uint64_t runtime_oof_pid(void) { return 4294967297ull; }
uint32_t runtime_oof_refresh_token(void) { return 1; }
size_t runtime_oof_token(char *out, size_t cap) { (void)out; (void)cap; return 0; }
void hle_bind_system_event(void *event, int autoclear) { (void)event; (void)autoclear; }
void hle_signal_system_event(void *event) { (void)event; }

static const char *name_now(void) {
    static char out[33]; Uid uid; uint64_t raw[2]; runtime_account_uid(raw); memcpy(&uid, raw, sizeof uid);
    uint32_t (*get)(char *, const Uid *) = (uint32_t (*)(char *, const Uid *))runtime_account_override("_ZN2nn7account11GetNicknameEPNS0_8NicknameERKNS0_3UidE");
    assert(get && get(out, &uid) == 0 && out[32] == 0);
    return out;
}

int main(int argc, char **argv) {
    assert(argc == 2); setenv("TOTK_PROFILE_DIR", argv[1], 1);
    setenv("OOF_USERNAME", "Ana.1", 1);
    assert(!strcmp(name_now(), "Local"));      /* offline: the profile's name */
    online = 1;
    assert(!strcmp(name_now(), "Ana.1"));      /* online: the account's user name */
    for (const char *bad = "ab"; bad; bad = !strcmp(bad, "ab") ? "elevenchars" : !strcmp(bad, "elevenchars") ? "a b" : NULL) {
        setenv("OOF_USERNAME", bad, 1); assert(!strcmp(name_now(), "Local"));
    }
    unsetenv("OOF_USERNAME"); assert(!strcmp(name_now(), "Local"));
    puts("pass: nickname");
    return 0;
}
