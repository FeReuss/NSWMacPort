/* nn::ec (applet_host.c): shop pages report closed, the shop service fails without IPC. No game. */
#include "../../runtime/applet_host.c"
#include <assert.h>

_Noreturn void runtime_fail(const char *why) { fprintf(stderr, "FAIL: %s\n", why); exit(1); }
int runtime_core_away(void) { return 1; }
void runtime_core_back(int *away) { *away = 0; }
void hle_bind_system_event(void *event, int autoclear) { (void)event; (void)autoclear; }
void hle_signal_system_event(void *event) { (void)event; }
uint64_t runtime_now_ns(void) { return 0; }

static unsigned call(const char *name) {
    unsigned (*fn)(void) = (unsigned (*)(void))runtime_applet_override(name);
    if (!fn) { fprintf(stderr, "not bound: %s\n", name); exit(1); }
    return fn();
}

int main(void) {
    const char *pages[] = {"_ZN2nn2ec19ShowShopProductListEPKNS0_5NsUidEiPKcRKNS_7account10UserHandleE",
                           "_ZN2nn2ec22ShowShopProductDetailsENS0_5NsUidERKNS_7account10UserHandleE",
                           "_ZN2nn2ec24ShowShopAddOnContentListENS_13ApplicationIdE",
                           "_ZN2nn2ec24ShowShopAddOnContentListENS_13ApplicationIdERKNS_7account10UserHandleE"};
    for (unsigned i = 0; i < 4; i++) assert(call(pages[i]) == 0);
    assert(call("_ZN2nn2ec33InitializeForShopServiceAccessorsEPvm") == 0);
    unsigned r = call("_ZN2nn2ec19ShopServiceAccessor10InitializeERKNS0_11ShopServiceE");
    assert(r && (r & 0x1ff) == 164);   // a failure in nn::ec's module
    assert(call("_ZN2nn2ec19ShopServiceAccessor7RequestEPNS1_13AsyncResponseERKNS_7account3UidENS0_11ShopService6MethodEPKcRKNS1_8PostDataENS_8TimeSpanE") == r);
    puts("pass: no shop");
    return 0;
}
