/* nn::ldn (local wireless) on the host, over the local network: Smash's "Juego local" opened ldn:u and closed the game
   (2026-10-24). The Mac has no Switch radio, so the "wireless network" is the LAN: the access point answers scans sent as
   UDP broadcasts (and to 127.0.0.1, for a second game on the same Mac) on port 11452, stations join over TCP to the port
   in the answer, and the access point pushes its NetworkInfo (nodes, advertise data) to every station when it changes.
   Node addresses are the Macs' own IPv4 addresses, so the game's own traffic (Pia over nn::socket, net_host.c) goes
   straight between them. Structures follow libnx's ldn.h (NetworkInfo 0x480 bytes) and SDK 8.2.1: Ipv4Address is a
   host-order u32 (MakeIpv4Address, sdk+0x2bda70); MakeIpv4Address / MakeBroadcastAddress stay the SDK's (pure code).
   shortcut: no passphrase check or encryption between Macs (the LAN is trusted); add it if networks must be private. */
#include "runtime.h"
#include "game_version.h"
#include <arpa/inet.h>
#include <errno.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

enum { S_NONE, S_INITIALIZED, S_ACCESS_POINT, S_AP_CREATED, S_STATION, S_STATION_CONNECTED, S_ERROR };
enum { R_DONE = 0, R_INVALID_STATE = 0x40cb, R_NODE_NOT_FOUND = 0x60cb, R_CONNECT_FAILURE = 0x80cb, R_CONNECT_NOT_FOUND = 0x82cb,
       R_CONNECT_REJECTED = 0x86cb, R_INVALID_ARGUMENT = 0xc0cb, R_TOO_MANY_PLAYERS = 0x120cb };   /* ldn module 203 */
enum { PORT = 11452, MAX_NODES = 8, ADVERTISE_MAX = 0x180 };

typedef struct { int64_t local_communication_id; uint8_t r0[2]; uint16_t scene_id; uint8_t r1[4]; } IntentId;
typedef struct { IntentId intent; uint8_t session[16]; } NetworkId;
typedef struct { uint32_t ip; uint8_t mac[6]; int8_t node_id; uint8_t connected; char user[0x21]; uint8_t platform; int16_t version; uint8_t r[0x10]; } NodeInfo;
typedef struct {
    NetworkId id;
    uint8_t bssid[6]; uint8_t ssid_len; char ssid[0x21]; int16_t channel; int8_t link_level; uint8_t network_type; uint8_t r0[4];
    uint8_t server_random[16]; uint16_t security_mode; uint8_t accept_policy, version, r1[2]; int8_t node_count_max; uint8_t node_count;
    NodeInfo nodes[MAX_NODES]; uint8_t r2[2]; uint16_t advertise_size; uint8_t advertise[ADVERTISE_MAX]; uint8_t r3[0x8c]; uint64_t r4;
} NetworkInfo;
typedef struct { NetworkId id; uint32_t network_type; uint8_t bssid[6]; uint8_t ssid_len; char ssid[0x21]; uint8_t r[0x10]; uint32_t flags; } ScanFilter;
typedef struct { uint16_t mode, passphrase_size; uint8_t passphrase[0x40]; } SecurityConfig;
typedef struct { uint8_t server_random[16], session[16]; } SecurityParameter;
typedef struct { IntentId intent; int16_t channel; int8_t node_count_max; uint8_t r0; int16_t version; uint8_t r1[0xa]; } NetworkConfig;
typedef struct { char user[0x21]; uint8_t r[0xf]; } UserConfig;
_Static_assert(sizeof(NodeInfo) == 0x40 && sizeof(NetworkInfo) == 0x480 && sizeof(ScanFilter) == 0x60, "ldn NodeInfo / NetworkInfo / ScanFilter");
_Static_assert(sizeof(SecurityConfig) == 0x44 && sizeof(NetworkConfig) == 0x20 && sizeof(UserConfig) == 0x30, "ldn configs");

/* Wire messages: magic, type, then the payload. UDP: scan (local communication id) / answer (TCP port + NetworkInfo).
   TCP: join (user name, version) / welcome (node id + NetworkInfo) / update (NetworkInfo). */
enum { MSG_SCAN = 1, MSG_ANSWER, MSG_JOIN, MSG_WELCOME, MSG_UPDATE };
typedef struct { char magic[8]; uint32_t type, port; } Header;
static const char MAGIC[8] = {'S', 'S', 'B', 'U', 'L', 'D', 'N', '1'};

static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static int state, reason, accept_policy;
static NetworkInfo net;                         /* the access point's own network, or the one a station joined */
static int udp = -1, listener = -1, ap_link = -1, links[MAX_NODES];   /* links[i]: access point's TCP to node i (0 unused) */
static uint16_t tcp_port;
static void *state_event;
static pthread_t worker; static bool worker_on;

static void changed(void) { if (state_event) hle_signal_system_event(state_event); }
static bool local_ipv4(uint32_t *ip, uint32_t *mask) {
    struct ifaddrs *list, *i; *ip = 0x7f000001; *mask = 0xff000000;
    if (getifaddrs(&list)) return false;
    for (i = list; i; i = i->ifa_next)
        if (i->ifa_addr && i->ifa_addr->sa_family == AF_INET && (i->ifa_flags & IFF_UP) && (i->ifa_flags & IFF_RUNNING) && !(i->ifa_flags & IFF_LOOPBACK)) {
            *ip = ntohl(((struct sockaddr_in *)(void *)i->ifa_addr)->sin_addr.s_addr);
            *mask = i->ifa_netmask ? ntohl(((struct sockaddr_in *)(void *)i->ifa_netmask)->sin_addr.s_addr) : 0xffffff00; break;
        }
    freeifaddrs(list); return true;
}
static bool send_all(int fd, const void *p, size_t n) { const uint8_t *b = p; while (n) { ssize_t k = send(fd, b, n, 0); if (k <= 0) { if (k < 0 && errno == EINTR) continue; return false; } b += k; n -= (size_t)k; } return true; }
static bool recv_all(int fd, void *p, size_t n) { uint8_t *b = p; while (n) { ssize_t k = recv(fd, b, n, 0); if (k <= 0) { if (k < 0 && errno == EINTR) continue; return false; } b += k; n -= (size_t)k; } return true; }
static bool send_msg(int fd, uint32_t type, const void *payload, size_t n) {
    Header h; memcpy(h.magic, MAGIC, 8); h.type = type; h.port = tcp_port; return send_all(fd, &h, sizeof h) && (!n || send_all(fd, payload, n));
}
static size_t payload_size(uint32_t type) { return type == MSG_JOIN ? sizeof(NodeInfo) : type == MSG_WELCOME ? 1 + sizeof(NetworkInfo) : type == MSG_UPDATE ? sizeof(NetworkInfo) : 0; }
static void push_update_locked(void) { for (int i = 1; i < MAX_NODES; i++) if (links[i] >= 0) send_msg(links[i], MSG_UPDATE, &net, sizeof net); }
static void recount_locked(void) { net.node_count = 0; for (int i = 0; i < MAX_NODES; i++) if (net.nodes[i].connected) net.node_count++; }
static void drop_node_locked(int i) {
    if (links[i] >= 0) { close(links[i]); links[i] = -1; }
    memset(&net.nodes[i], 0, sizeof net.nodes[i]); recount_locked(); push_update_locked();
}

/* Network thread: answers scans and joins (access point), follows the access point (station). */
static void accept_join_locked(void) {
    struct sockaddr_in from; socklen_t fl = sizeof from; int fd = accept(listener, (struct sockaddr *)&from, &fl); if (fd < 0) return;
    int one = 1; setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    struct timeval tv = {2, 0}; setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    Header h; NodeInfo who;
    if (!recv_all(fd, &h, sizeof h) || memcmp(h.magic, MAGIC, 8) || h.type != MSG_JOIN || !recv_all(fd, &who, sizeof who)) { close(fd); return; }
    int slot = 0; for (int i = 1; i < MAX_NODES && i < net.node_count_max && !slot; i++) if (!net.nodes[i].connected) slot = i;
    if (!slot || accept_policy == 1 || state != S_AP_CREATED) { close(fd); return; }   /* full, or AlwaysReject */
    tv.tv_sec = 0; setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    who.node_id = (int8_t)slot; who.connected = 1; who.user[0x20] = 0; net.nodes[slot] = who; recount_locked(); links[slot] = fd;
    uint8_t welcome[1 + sizeof(NetworkInfo)]; welcome[0] = (uint8_t)slot; memcpy(welcome + 1, &net, sizeof net);
    if (!send_msg(fd, MSG_WELCOME, welcome, sizeof welcome)) { drop_node_locked(slot); return; }
    push_update_locked();
    fprintf(stderr, "[ldn] node %d joined: %s (%u.%u.%u.%u)\n", slot, who.user, who.ip >> 24, who.ip >> 16 & 255, who.ip >> 8 & 255, who.ip & 255);
}
static void *network(void *arg) {
    (void)arg;
    for (;;) {
        struct pollfd p[2 + MAX_NODES]; int n = 0, kind[2 + MAX_NODES];
        pthread_mutex_lock(&mu);
        if (!worker_on) { pthread_mutex_unlock(&mu); return NULL; }
        if (udp >= 0) { p[n] = (struct pollfd){udp, POLLIN, 0}; kind[n++] = -1; }
        if (listener >= 0) { p[n] = (struct pollfd){listener, POLLIN, 0}; kind[n++] = -2; }
        if (ap_link >= 0) { p[n] = (struct pollfd){ap_link, POLLIN, 0}; kind[n++] = -3; }
        for (int i = 1; i < MAX_NODES; i++) if (links[i] >= 0) { p[n] = (struct pollfd){links[i], POLLIN, 0}; kind[n++] = i; }
        pthread_mutex_unlock(&mu);
        if (poll(p, (nfds_t)n, 100) <= 0) continue;
        pthread_mutex_lock(&mu);
        for (int k = 0; k < n; k++) {
            if (!(p[k].revents & (POLLIN | POLLHUP | POLLERR))) continue;
            if (kind[k] == -1 && udp >= 0) {   /* a scan: answer while our network is open */
                uint8_t b[64]; struct sockaddr_in from; socklen_t fl = sizeof from;
                ssize_t got = recvfrom(udp, b, sizeof b, 0, (struct sockaddr *)&from, &fl);
                if (got >= (ssize_t)(sizeof(Header) + 8) && !memcmp(b, MAGIC, 8) && ((Header *)(void *)b)->type == MSG_SCAN && state == S_AP_CREATED) {
                    uint8_t answer[sizeof(Header) + sizeof(NetworkInfo)]; Header h; memcpy(h.magic, MAGIC, 8); h.type = MSG_ANSWER; h.port = tcp_port;
                    memcpy(answer, &h, sizeof h); memcpy(answer + sizeof h, &net, sizeof net);
                    sendto(udp, answer, sizeof answer, 0, (struct sockaddr *)&from, fl);
                }
            } else if (kind[k] == -2 && listener >= 0) accept_join_locked();
            else if (kind[k] == -3 && ap_link >= 0) {   /* station: updates from the access point, or its loss */
                Header h; size_t m; uint8_t body[1 + sizeof(NetworkInfo)];
                bool ok = recv_all(ap_link, &h, sizeof h) && !memcmp(h.magic, MAGIC, 8) && (m = payload_size(h.type)) && recv_all(ap_link, body, m);
                if (ok && h.type == MSG_UPDATE) { memcpy(&net, body, sizeof net); changed(); }
                else if (!ok) { close(ap_link); ap_link = -1; if (state == S_STATION_CONNECTED) { state = S_STATION; reason = 4; fprintf(stderr, "[ldn] access point gone\n"); changed(); } }
            } else if (kind[k] > 0 && links[kind[k]] >= 0) {   /* access point: a station left (it sends nothing else) */
                uint8_t b[64]; ssize_t got = recv(links[kind[k]], b, sizeof b, 0);
                if (got <= 0) { fprintf(stderr, "[ldn] node %d left\n", kind[k]); drop_node_locked(kind[k]); changed(); }
            }
        }
        pthread_mutex_unlock(&mu);
    }
}

static int udp_socket(bool listen_port) {
    int fd = socket(AF_INET, SOCK_DGRAM, 0), one = 1; if (fd < 0) return -1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one); setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &one, sizeof one);
    setsockopt(fd, SOL_SOCKET, SO_BROADCAST, &one, sizeof one);
    struct sockaddr_in a = {.sin_len = sizeof a, .sin_family = AF_INET, .sin_port = htons(listen_port ? PORT : 0), .sin_addr.s_addr = htonl(INADDR_ANY)};
    if (bind(fd, (struct sockaddr *)&a, sizeof a)) { close(fd); return -1; }
    return fd;
}
static void close_network_locked(int why) {
    for (int i = 1; i < MAX_NODES; i++) if (links[i] >= 0) { close(links[i]); links[i] = -1; }
    if (listener >= 0) { close(listener); listener = -1; } if (udp >= 0) { close(udp); udp = -1; }
    if (ap_link >= 0) { close(ap_link); ap_link = -1; }
    memset(&net, 0, sizeof net); reason = why;
}

static uint32_t initialize(void) {
    pthread_mutex_lock(&mu);
    if (state == S_NONE) { state = S_INITIALIZED; reason = 0; for (int i = 0; i < MAX_NODES; i++) links[i] = -1;
        if (!worker_on) { worker_on = true; if (pthread_create(&worker, NULL, network, NULL)) runtime_fail("ldn network thread"); } }
    pthread_mutex_unlock(&mu); return R_DONE;
}
static void finalize(void) {
    pthread_mutex_lock(&mu); close_network_locked(0); state = S_NONE; worker_on = false; pthread_mutex_unlock(&mu);
    pthread_join(worker, NULL);
}
static int get_state(void) { pthread_mutex_lock(&mu); int s = state; pthread_mutex_unlock(&mu); return s; }
int runtime_ldn_active(void) { int s = get_state(); return s == S_AP_CREATED || s == S_STATION_CONNECTED; }
static int disconnect_reason(void) { pthread_mutex_lock(&mu); int r = reason; pthread_mutex_unlock(&mu); return r; }
static void attach_state_event(void *event) { if (!event) runtime_fail("ldn state event"); hle_bind_system_event(event, 1); pthread_mutex_lock(&mu); state_event = event; pthread_mutex_unlock(&mu); }
static uint32_t move(int from, int to) {
    pthread_mutex_lock(&mu); uint32_t r = state == from ? R_DONE : R_INVALID_STATE; if (!r) { state = to; reason = 0; } pthread_mutex_unlock(&mu);
    if (!r) changed(); return r;
}
static uint32_t open_ap(void) { return move(S_INITIALIZED, S_ACCESS_POINT); }
static uint32_t open_station(void) { return move(S_INITIALIZED, S_STATION); }
static uint32_t leave(int connected, int open, int to, int why) {
    pthread_mutex_lock(&mu); uint32_t r = R_DONE;
    if (state == connected) { close_network_locked(why); state = open; }
    if (to != open) { if (state == open) state = to; else r = R_INVALID_STATE; }
    else if (state != open) r = R_INVALID_STATE;
    pthread_mutex_unlock(&mu); changed(); return r;
}
static uint32_t destroy_network(void) { return leave(S_AP_CREATED, S_ACCESS_POINT, S_ACCESS_POINT, 3); }
static uint32_t close_ap(void) { return leave(S_AP_CREATED, S_ACCESS_POINT, S_INITIALIZED, 3); }
static uint32_t disconnect(void) { return leave(S_STATION_CONNECTED, S_STATION, S_STATION, 1); }
static uint32_t close_station(void) { return leave(S_STATION_CONNECTED, S_STATION, S_INITIALIZED, 1); }

static void random_bytes(void *p, size_t n) { arc4random_buf(p, n); }
static uint32_t create(const NetworkConfig *cfg, const SecurityConfig *sec, const SecurityParameter *param, const UserConfig *user) {
    if (!cfg || !sec || !user || cfg->node_count_max < 1 || cfg->node_count_max > MAX_NODES || cfg->version < 0) return R_INVALID_ARGUMENT;
    pthread_mutex_lock(&mu);
    if (state != S_ACCESS_POINT) { pthread_mutex_unlock(&mu); return R_INVALID_STATE; }
    uint16_t keep_size = net.advertise_size; uint8_t keep[ADVERTISE_MAX]; memcpy(keep, net.advertise, sizeof keep);
    memset(&net, 0, sizeof net);
    net.id.intent = cfg->intent; if (net.id.intent.local_communication_id == -1) net.id.intent.local_communication_id = (int64_t)GAME_TITLE_ID;
    if (param) { memcpy(net.id.session, param->session, 16); memcpy(net.server_random, param->server_random, 16); }
    else { random_bytes(net.id.session, 16); random_bytes(net.server_random, 16); }
    random_bytes(net.bssid, 6); net.bssid[0] = (uint8_t)((net.bssid[0] & 0xfe) | 2);   /* locally administered */
    net.ssid_len = 32; for (int i = 0; i < 16; i++) snprintf(net.ssid + 2 * i, 3, "%02x", net.id.session[i]);
    net.channel = cfg->channel ? cfg->channel : 6; net.link_level = 3; net.network_type = 2;
    net.security_mode = sec->mode ? sec->mode : 1; net.accept_policy = (uint8_t)accept_policy; net.version = 3;
    net.node_count_max = cfg->node_count_max; net.advertise_size = keep_size; memcpy(net.advertise, keep, sizeof keep);
    uint32_t ip, mask; local_ipv4(&ip, &mask); (void)mask;
    NodeInfo *me = &net.nodes[0]; me->ip = ip; memcpy(me->mac, net.bssid, 6); me->node_id = 0; me->connected = 1;
    memcpy(me->user, user->user, 0x20); me->version = cfg->version; recount_locked();
    udp = udp_socket(true); listener = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in a = {.sin_len = sizeof a, .sin_family = AF_INET, .sin_addr.s_addr = htonl(INADDR_ANY)}; socklen_t al = sizeof a;
    if (udp < 0 || listener < 0 || bind(listener, (struct sockaddr *)&a, sizeof a) || listen(listener, 8) || getsockname(listener, (struct sockaddr *)&a, &al)) {
        close_network_locked(0); pthread_mutex_unlock(&mu); fprintf(stderr, "[ldn] cannot open the LAN network (port %d)\n", PORT); return R_CONNECT_FAILURE;
    }
    tcp_port = ntohs(a.sin_port); state = S_AP_CREATED;
    fprintf(stderr, "[ldn] network created by %s: %d players at most, LAN %u.%u.%u.%u (scan port %d, join port %u)\n", me->user, net.node_count_max, ip >> 24, ip >> 16 & 255, ip >> 8 & 255, ip & 255, PORT, tcp_port);
    pthread_mutex_unlock(&mu); changed(); return R_DONE;
}
static uint32_t create_network(const NetworkConfig *cfg, const SecurityConfig *sec, const UserConfig *user) { return create(cfg, sec, NULL, user); }
static uint32_t create_private(const NetworkConfig *cfg, const SecurityConfig *sec, const SecurityParameter *param, const UserConfig *user, int n, const void *addresses) {
    (void)n; (void)addresses; return param ? create(cfg, sec, param, user) : R_INVALID_ARGUMENT;
}
static uint32_t set_advertise(const void *data, size_t size) {
    if (size > ADVERTISE_MAX || (size && !data)) return R_INVALID_ARGUMENT;
    pthread_mutex_lock(&mu); net.advertise_size = (uint16_t)size; if (size) memcpy(net.advertise, data, size); if (state == S_AP_CREATED) push_update_locked(); pthread_mutex_unlock(&mu);
    return R_DONE;
}
static uint32_t set_accept_policy(int policy) {
    if (policy < 0 || policy > 3) return R_INVALID_ARGUMENT;
    pthread_mutex_lock(&mu); accept_policy = policy; net.accept_policy = (uint8_t)policy; pthread_mutex_unlock(&mu); return R_DONE;
}
static uint32_t reject(uint32_t ip) {
    pthread_mutex_lock(&mu); uint32_t r = state == S_AP_CREATED ? R_NODE_NOT_FOUND : R_INVALID_STATE;
    for (int i = 1; i < MAX_NODES && r == R_NODE_NOT_FOUND; i++) if (net.nodes[i].connected && net.nodes[i].ip == ip) { drop_node_locked(i); r = R_DONE; }
    pthread_mutex_unlock(&mu); if (!r) changed(); return r;
}
static uint32_t controller_restriction(int on) { (void)on; return R_DONE; }

static bool matches(const NetworkInfo *n, const ScanFilter *f) {
    if (!f) return true;
    if ((f->flags & 1) && n->id.intent.local_communication_id != f->id.intent.local_communication_id) return false;
    if ((f->flags & 2) && memcmp(n->id.session, f->id.session, 16)) return false;
    if ((f->flags & 4) && n->network_type != f->network_type) return false;
    if ((f->flags & 8) && memcmp(n->bssid, f->bssid, 6)) return false;
    if ((f->flags & 16) && (n->ssid_len != f->ssid_len || memcmp(n->ssid, f->ssid, n->ssid_len))) return false;
    if ((f->flags & 32) && n->id.intent.scene_id != f->id.intent.scene_id) return false;
    return true;
}
/* Ask the LAN (and this Mac) for open networks for 300 ms; the answers carry the join port. */
static int scan_lan(NetworkInfo *out, uint16_t *ports, int max, const ScanFilter *f) {
    int fd = udp_socket(false), n = 0; if (fd < 0) return 0;
    uint8_t ask[sizeof(Header) + 8]; Header h; memcpy(h.magic, MAGIC, 8); h.type = MSG_SCAN; h.port = 0;
    int64_t id = f && (f->flags & 1) ? f->id.intent.local_communication_id : (int64_t)GAME_TITLE_ID; memcpy(ask, &h, sizeof h); memcpy(ask + sizeof h, &id, 8);
    uint32_t targets[2] = {INADDR_BROADCAST, INADDR_LOOPBACK};
    for (int t = 0; t < 2; t++) { struct sockaddr_in a = {.sin_len = sizeof a, .sin_family = AF_INET, .sin_port = htons(PORT), .sin_addr.s_addr = htonl(targets[t])}; sendto(fd, ask, sizeof ask, 0, (struct sockaddr *)&a, sizeof a); }
    uint64_t end = runtime_now_ns() + 300000000ull;
    while (n < max) {
        uint64_t now = runtime_now_ns(); if (now >= end) break;
        struct pollfd p = {fd, POLLIN, 0}; if (poll(&p, 1, (int)((end - now) / 1000000) + 1) <= 0) continue;
        uint8_t b[sizeof(Header) + sizeof(NetworkInfo)]; ssize_t got = recv(fd, b, sizeof b, 0);
        if (got != (ssize_t)sizeof b || memcmp(b, MAGIC, 8) || ((Header *)(void *)b)->type != MSG_ANSWER) continue;
        NetworkInfo found; memcpy(&found, b + sizeof(Header), sizeof found); if (!matches(&found, f)) continue;
        bool dup = false; for (int i = 0; i < n; i++) dup |= !memcmp(out[i].bssid, found.bssid, 6);   /* broadcast and loopback both answered */
        if (!dup) { ports[n] = (uint16_t)((Header *)(void *)b)->port; out[n++] = found; }
    }
    close(fd); return n;
}
static uint32_t scan(NetworkInfo *out, int *count, int max, const ScanFilter *f, int channel) {
    (void)channel; if (!out || !count || max < 0) return R_INVALID_ARGUMENT;
    int s = get_state(); if (s != S_STATION && s != S_ACCESS_POINT && s != S_AP_CREATED) return R_INVALID_STATE;
    uint16_t ports[64]; if (max > 64) max = 64;
    *count = scan_lan(out, ports, max, f); return R_DONE;
}
static uint32_t join(const NetworkInfo *target, uint16_t port, const UserConfig *user, int version) {
    int fd = socket(AF_INET, SOCK_STREAM, 0); if (fd < 0) return R_CONNECT_FAILURE;
    struct sockaddr_in a = {.sin_len = sizeof a, .sin_family = AF_INET, .sin_port = htons(port), .sin_addr.s_addr = htonl(target->nodes[0].ip)};
    struct timeval tv = {3, 0}; setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv); setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    int one = 1; setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    if (connect(fd, (struct sockaddr *)&a, sizeof a)) {   /* the access point may be this Mac reached through loopback */
        a.sin_addr.s_addr = htonl(INADDR_LOOPBACK); close(fd); fd = socket(AF_INET, SOCK_STREAM, 0);
        if (fd < 0 || connect(fd, (struct sockaddr *)&a, sizeof a)) { if (fd >= 0) close(fd); return R_CONNECT_NOT_FOUND; }
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv); setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    }
    NodeInfo me = {0}; uint32_t ip, mask; local_ipv4(&ip, &mask); me.ip = ip; arc4random_buf(me.mac, 6); me.mac[0] = (uint8_t)((me.mac[0] & 0xfe) | 2);
    memcpy(me.user, user->user, 0x20); me.version = (int16_t)version;
    Header h; uint8_t welcome[1 + sizeof(NetworkInfo)];
    if (!send_msg(fd, MSG_JOIN, &me, sizeof me) || !recv_all(fd, &h, sizeof h) || memcmp(h.magic, MAGIC, 8) || h.type != MSG_WELCOME || !recv_all(fd, welcome, sizeof welcome)) {
        close(fd); return R_CONNECT_REJECTED;
    }
    tv.tv_sec = 0; setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    pthread_mutex_lock(&mu);
    if (state != S_STATION) { pthread_mutex_unlock(&mu); close(fd); return R_INVALID_STATE; }
    memcpy(&net, welcome + 1, sizeof net); ap_link = fd; state = S_STATION_CONNECTED; reason = 0;
    pthread_mutex_unlock(&mu);
    fprintf(stderr, "[ldn] joined %s's network as node %d\n", target->nodes[0].user, welcome[0]);
    changed(); return R_DONE;
}
static uint32_t connect_network(const NetworkInfo *target, const SecurityConfig *sec, const UserConfig *user, int version, int option) {
    (void)sec; (void)option; if (!target || !user) return R_INVALID_ARGUMENT;
    if (get_state() != S_STATION) return R_INVALID_STATE;
    NetworkInfo found[8]; uint16_t ports[8]; ScanFilter f = {0}; memcpy(f.bssid, target->bssid, 6); f.flags = 8;
    int n = scan_lan(found, ports, 8, &f); if (!n) return R_CONNECT_NOT_FOUND;   /* the answer gives the join port */
    if (found[0].node_count >= found[0].node_count_max) return R_TOO_MANY_PLAYERS;
    return join(&found[0], ports[0], user, version);
}
static uint32_t connect_private(const NetworkConfig *cfg, const SecurityConfig *sec, const SecurityParameter *param, const UserConfig *user, int version, int option) {
    (void)sec; (void)option; if (!cfg || !param || !user) return R_INVALID_ARGUMENT;
    if (get_state() != S_STATION) return R_INVALID_STATE;
    NetworkInfo found[8]; uint16_t ports[8]; ScanFilter f = {0}; memcpy(f.id.session, param->session, 16); f.flags = 2;
    int n = scan_lan(found, ports, 8, &f); if (!n) return R_CONNECT_NOT_FOUND;
    return join(&found[0], ports[0], user, version);
}
static uint32_t network_info(NetworkInfo *out) {
    if (!out) return R_INVALID_ARGUMENT;
    pthread_mutex_lock(&mu); uint32_t r = state == S_AP_CREATED || state == S_STATION_CONNECTED ? R_DONE : R_INVALID_STATE; if (!r) *out = net; pthread_mutex_unlock(&mu); return r;
}
static uint32_t ipv4_address(uint32_t *ip, uint32_t *mask) {
    if (!ip || !mask) return R_INVALID_ARGUMENT;
    int s = get_state(); if (s != S_AP_CREATED && s != S_STATION_CONNECTED) return R_INVALID_STATE;
    local_ipv4(ip, mask); return R_DONE;
}
static uint32_t network_config(NetworkConfig *out) {
    if (!out) return R_INVALID_ARGUMENT;
    pthread_mutex_lock(&mu); uint32_t r = state == S_AP_CREATED || state == S_STATION_CONNECTED ? R_DONE : R_INVALID_STATE;
    if (!r) { memset(out, 0, sizeof *out); out->intent = net.id.intent; out->channel = net.channel; out->node_count_max = net.node_count_max; out->version = net.nodes[0].version; }
    pthread_mutex_unlock(&mu); return r;
}
static uint32_t security_parameter(SecurityParameter *out) {
    if (!out) return R_INVALID_ARGUMENT;
    pthread_mutex_lock(&mu); uint32_t r = state == S_AP_CREATED || state == S_STATION_CONNECTED ? R_DONE : R_INVALID_STATE;
    if (!r) { memcpy(out->server_random, net.server_random, 16); memcpy(out->session, net.id.session, 16); }
    pthread_mutex_unlock(&mu); return r;
}

void *runtime_ldn_override(const char *name) {
    static const struct { const char *name; void *fn; } entries[] = {
        {"_ZN2nn3ldn10InitializeEv", initialize}, {"_ZN2nn3ldn8FinalizeEv", finalize}, {"_ZN2nn3ldn8GetStateEv", get_state},
        {"_ZN2nn3ldn19GetDisconnectReasonEv", disconnect_reason}, {"_ZN2nn3ldn22AttachStateChangeEventEPNS_2os15SystemEventTypeE", attach_state_event},
        {"_ZN2nn3ldn15OpenAccessPointEv", open_ap}, {"_ZN2nn3ldn16CloseAccessPointEv", close_ap}, {"_ZN2nn3ldn14DestroyNetworkEv", destroy_network},
        {"_ZN2nn3ldn11OpenStationEv", open_station}, {"_ZN2nn3ldn12CloseStationEv", close_station}, {"_ZN2nn3ldn10DisconnectEv", disconnect},
        {"_ZN2nn3ldn13CreateNetworkERKNS0_13NetworkConfigERKNS0_14SecurityConfigERKNS0_10UserConfigE", create_network},
        {"_ZN2nn3ldn20CreateNetworkPrivateERKNS0_13NetworkConfigERKNS0_14SecurityConfigERKNS0_17SecurityParameterERKNS0_10UserConfigEiPKNS0_12AddressEntryE", create_private},
        {"_ZN2nn3ldn16SetAdvertiseDataEPKvm", set_advertise}, {"_ZN2nn3ldn22SetStationAcceptPolicyENS0_12AcceptPolicyE", set_accept_policy},
        {"_ZN2nn3ldn6RejectENS0_11Ipv4AddressE", reject},
        {"_ZN2nn3ldn32SetWirelessControllerRestrictionENS0_29WirelessControllerRestrictionE", controller_restriction},
        {"_ZN2nn3ldn4ScanEPNS0_11NetworkInfoEPiiRKNS0_10ScanFilterEi", scan}, {"_ZN2nn3ldn11ScanPrivateEPNS0_11NetworkInfoEPiiRKNS0_10ScanFilterEi", scan},
        {"_ZN2nn3ldn7ConnectERKNS0_11NetworkInfoERKNS0_14SecurityConfigERKNS0_10UserConfigEiNS0_13ConnectOptionE", connect_network},
        {"_ZN2nn3ldn14ConnectPrivateERKNS0_13NetworkConfigERKNS0_14SecurityConfigERKNS0_17SecurityParameterERKNS0_10UserConfigEiNS0_13ConnectOptionE", connect_private},
        {"_ZN2nn3ldn14GetNetworkInfoEPNS0_11NetworkInfoE", network_info}, {"_ZN2nn3ldn14GetIpv4AddressEPNS0_11Ipv4AddressEPNS0_10SubnetMaskE", ipv4_address},
        {"_ZN2nn3ldn16GetNetworkConfigEPNS0_13NetworkConfigE", network_config}, {"_ZN2nn3ldn20GetSecurityParameterEPNS0_17SecurityParameterE", security_parameter},
    };
    for (size_t i = 0; i < sizeof entries / sizeof *entries; i++) if (!strcmp(name, entries[i].name)) return entries[i].fn;
    return NULL;
}
