/* nn::ldn over the LAN through the host: a forked access point creates a network with advertise data; this process
   scans (filter by local communication id), joins as node 1, sees both nodes and the data, then the access point
   rejects it and the station falls back to Station with the event signaled. */
#include "../../runtime/ldn_host.c"
#include <assert.h>
#include <signal.h>
#include <sys/wait.h>
#include <time.h>

_Noreturn void runtime_fail(const char *why) { fprintf(stderr, "FAIL: %s\n", why); exit(1); }
uint64_t runtime_now_ns(void) { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }
static _Atomic int signals;
void hle_bind_system_event(void *e, int autoclear) { (void)e; (void)autoclear; }
void hle_signal_system_event(void *e) { (void)e; signals++; }
static void *need(const char *n) { void *f = runtime_ldn_override(n); assert(f); return f; }
#define F(type, name, mangled) __typeof__(type) name = need(mangled)

int main(void) {
    F(uint32_t (*)(void), init, "_ZN2nn3ldn10InitializeEv");
    F(int (*)(void), state_now, "_ZN2nn3ldn8GetStateEv");
    F(void (*)(void *), attach, "_ZN2nn3ldn22AttachStateChangeEventEPNS_2os15SystemEventTypeE");
    F(uint32_t (*)(void), ap, "_ZN2nn3ldn15OpenAccessPointEv");
    F(uint32_t (*)(void), sta, "_ZN2nn3ldn11OpenStationEv");
    F(uint32_t (*)(const NetworkConfig *, const SecurityConfig *, const UserConfig *), mk, "_ZN2nn3ldn13CreateNetworkERKNS0_13NetworkConfigERKNS0_14SecurityConfigERKNS0_10UserConfigE");
    F(uint32_t (*)(const void *, size_t), adv, "_ZN2nn3ldn16SetAdvertiseDataEPKvm");
    F(uint32_t (*)(NetworkInfo *, int *, int, const ScanFilter *, int), find, "_ZN2nn3ldn4ScanEPNS0_11NetworkInfoEPiiRKNS0_10ScanFilterEi");
    F(uint32_t (*)(const NetworkInfo *, const SecurityConfig *, const UserConfig *, int, int), conn, "_ZN2nn3ldn7ConnectERKNS0_11NetworkInfoERKNS0_14SecurityConfigERKNS0_10UserConfigEiNS0_13ConnectOptionE");
    F(uint32_t (*)(NetworkInfo *), info, "_ZN2nn3ldn14GetNetworkInfoEPNS0_11NetworkInfoE");
    F(uint32_t (*)(uint32_t), rej, "_ZN2nn3ldn6RejectENS0_11Ipv4AddressE");
    F(int (*)(void), why, "_ZN2nn3ldn19GetDisconnectReasonEv");
    NetworkConfig cfg = {.intent = {.local_communication_id = -1, .scene_id = 7}, .node_count_max = 4, .version = 1};
    SecurityConfig sec = {.mode = 1, .passphrase_size = 16};
    int to_child[2]; assert(!pipe(to_child));
    pid_t child = fork(); assert(child >= 0);
    if (!child) {   /* access point */
        UserConfig host = {.user = "Host Mac"}; uint8_t ev[0x40];
        assert(!init()); attach(ev); assert(!ap()); assert(!mk(&cfg, &sec, &host)); assert(state_now() == S_AP_CREATED);
        assert(!adv("room:smash", 11)); assert(runtime_ldn_active());
        NetworkInfo n; for (int i = 0; i < 100; i++) { assert(!info(&n)); if (n.node_count == 2) break; usleep(50000); }
        assert(n.node_count == 2 && n.nodes[1].node_id == 1 && !strcmp(n.nodes[1].user, "Guest Mac"));
        char c; assert(read(to_child[0], &c, 1) == 1);   /* the station saw the network: reject it */
        assert(!rej(n.nodes[1].ip)); assert(!info(&n) && n.node_count == 1);
        sleep(1); _exit(0);
    }
    UserConfig guest = {.user = "Guest Mac"}; uint8_t ev[0x40];
    assert(!init()); attach(ev); assert(!sta()); assert(!runtime_ldn_active());
    NetworkInfo found[4]; int count = 0; ScanFilter f = {.id.intent.local_communication_id = (int64_t)GAME_TITLE_ID, .flags = 1};
    for (int i = 0; i < 20 && !count; i++) assert(!find(found, &count, 4, &f, 0));
    assert(count == 1 && found[0].node_count == 1 && found[0].id.intent.scene_id == 7 && !strcmp((char *)found[0].advertise, "room:smash"));
    assert(!conn(&found[0], &sec, &guest, 1, 0)); assert(state_now() == S_STATION_CONNECTED);
    NetworkInfo n; for (int i = 0; i < 40; i++) { assert(!info(&n)); if (n.node_count == 2) break; usleep(50000); }
    assert(n.node_count == 2 && !strcmp(n.nodes[0].user, "Host Mac"));
    int before = signals; assert(write(to_child[1], "x", 1) == 1);
    for (int i = 0; i < 60 && state_now() == S_STATION_CONNECTED; i++) usleep(50000);
    assert(state_now() == S_STATION && signals > before && why() == 4); assert(!runtime_ldn_active());
    int st; waitpid(child, &st, 0); assert(WIFEXITED(st) && !WEXITSTATUS(st));
    puts("ldn LAN network ok");
    return 0;
}
