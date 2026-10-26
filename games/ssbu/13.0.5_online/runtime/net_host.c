/* Online services under the ssbu-oof port standard v0.1 (ssbu-oof docs/STANDARD.md, sections 3-6): nifm, nsd,
   nn::socket on the host's BSD sockets, and nn::ssl's C API. Without OOF_SERVER / OOF_PID / OOF_DEVICE_KEY
   (oof_client.m) the game stays offline as before: nifm reports no network and no name resolves.
   Only the game server and NAT-check names resolve, always to the configured ssbu-oof server; nothing ever reaches
   a Nintendo host. TLS: OOF_TLS=1 (default) runs the game's TLS connection on the host's TLS stack (SecureTransport over
   the game's socket), validated against OOF_SERVER's host name with the system trust store or OOF_CA (PEM, the
   standard's oof.ca); OOF_TLS=0 passes the bytes through unencrypted (development, allowed by the standard).
   Switch sockets are FreeBSD's: sockaddr / addrinfo / pollfd layouts, fcntl and SOL_SOCKET values are the same as
   macOS's. AI_NUMERICSERV, the SOCK_NONBLOCK / SOCK_CLOEXEC type bits and errno differ: GetLastErrno numbers errors
   as Linux does (Pia's mapper main+0xb9de0 reads 11 = EAGAIN as "no data", anything unknown as a failure). */
#include "runtime.h"
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <netdb.h>
#include <netinet/in.h>
#include <poll.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <Security/SecureTransport.h>
#include <Security/Security.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

#define RESULT(module, description) ((uint32_t)(module) | ((uint32_t)(description) << 9))
#define NIFM_UNAVAILABLE RESULT(110, 300)
#define NIFM_HANDLED RESULT(110, 190)
#define SSL_WOULD_BLOCK RESULT(123, 204)   /* main compares nnsslConnectionRead's result with it (main+0x26f4c) */
#define SSL_IO_FAILED RESULT(123, 209)
#define SSL_NO_TLS RESULT(123, 300)
enum { SW_AI_NUMERICSERV = 0x8, SW_SOCK_NONBLOCK = 0x20000000, SW_SOCK_CLOEXEC = 0x10000000 };

/* ---------- the configured server ---------- */
static struct in_addr server_ip;
static uint16_t server_port;   /* host order */
static char server_host[256];  /* the name TLS validates (section 5) */
static int online(void) {
    static int state = -1; static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
    pthread_mutex_lock(&mu);
    if (state < 0) {
        state = 0; const char *s = getenv("OOF_SERVER"), *colon = s ? strrchr(s, ':') : NULL;
        if (runtime_oof_enabled() && colon && colon > s && colon - s < 256) {
            char host[256]; memcpy(host, s, (size_t)(colon - s)); host[colon - s] = 0;
            long port = strtol(colon + 1, NULL, 10);
            struct addrinfo hints = {.ai_family = AF_INET, .ai_socktype = SOCK_STREAM}, *res = NULL;
            if (port > 0 && port < 65536 && !getaddrinfo(host, NULL, &hints, &res) && res) {
                server_ip = ((struct sockaddr_in *)res->ai_addr)->sin_addr; server_port = (uint16_t)port; state = 1;
                memcpy(server_host, host, sizeof server_host);
                fprintf(stderr, "[oof-net] online through ssbu-oof %s (%s:%ld), TLS %s\n", s, inet_ntoa(server_ip), port,
                        getenv("OOF_TLS") && !strcmp(getenv("OOF_TLS"), "0") ? "off (plain bytes)" : "on");
            } else fprintf(stderr, "[oof-net] OOF_SERVER %s does not resolve: offline\n", s);
            if (res) freeaddrinfo(res);
        }
    }
    pthread_mutex_unlock(&mu);
    return state;
}
static int tls_off(void) { const char *t = getenv("OOF_TLS"); return t && !strcmp(t, "0"); }

/* ---------- nifm (section 3) ---------- */
static uint32_t ok(void) { return 0; }
static void nop(void) {}
static void nifm_request(void) {
    static int once; if (!once++) fprintf(stderr, "[totk-net] network request: %s\n", online() ? "online (ssbu-oof)" : "offline");
}
static uint8_t network_available(void) { return online() > 0; }
static uint8_t request_on_hold(void) { return 0; }
/* 0 = connected; nifm 190 = not connected, the error already dealt with (see the Odyssey note in system_services.c). */
static uint32_t request_result(void) { return online() ? 0 : NIFM_HANDLED; }
/* SDK 8.2.1 returns a bool here (sdk+0x322db0 "and w0, w0, #1"), whether the error was resolved and the request can be
   submitted again; a Result read as "resolved" whenever it was not 0. Nothing is resolved on the host. */
static bool request_error_result(void) { return false; }
static uint32_t proxy_setting(uint8_t *setting) {   /* ProxySetting starts with its "enabled" flag */
    if (!online()) return NIFM_UNAVAILABLE;
    if (!setting) runtime_fail("nifm proxy setting pointer");
    *setting = 0; return 0;
}
/* OOF_VIRTUAL_INDEX=<n> (diagnostics, up to 8 instances on one Mac, with the server's trust_client_address): the instance
   is 198.51.100.<n> (TEST-NET-2: neither private nor loopback for Pia, main+0xb2940 / 0xb2a30) and its UDP sockets take
   ports VIRT_PORT0 + 1000 * (n - 1) …, so a datagram's source port says which instance sent it. Datagrams to
   198.51.100.0/24 go to 127.0.0.1; datagrams from this Mac on an instance's ports come from its virtual address; NAT check
   answers carry the virtual address as the one seen. Instances on one Mac otherwise share an IP, and Pia then mistakes
   a peer for itself (its station table compares IPs only, main+0x19c010). */
enum { VIRT_PORT0 = 50000, VIRT_MAX = 8 };
#define VIRT_NET 0xc6336400u   /* 198.51.100.0 */
static int virt_index(void) { const char *e = getenv("OOF_VIRTUAL_INDEX"); int n = e ? atoi(e) : 0; return n >= 1 && n <= VIRT_MAX ? n : 0; }
static int virt_of_port(unsigned port) { return port >= VIRT_PORT0 && port < VIRT_PORT0 + 1000 * VIRT_MAX ? 1 + (int)(port - VIRT_PORT0) / 1000 : 0; }
/* The Mac's first up, non-loopback IPv4 interface (Pia advertises it to peers on the same LAN). */
static int lan_address(struct in_addr *ip, struct in_addr *mask) {
    /* OOF_UDP_SOURCE (see sock_bind): the instance's address is the one its UDP sockets use, so its private station URL
       matches where it receives (two instances on one Mac, one on the LAN address and one on loopback) */
    const char *source = getenv("OOF_UDP_SOURCE");
    if (source && inet_pton(AF_INET, source, ip) == 1) { if (mask) mask->s_addr = htonl(0xffffff00); return 1; }
    if (virt_index()) { ip->s_addr = htonl(VIRT_NET + (uint32_t)virt_index()); if (mask) mask->s_addr = htonl(0xffffff00); return 1; }
    struct ifaddrs *all = NULL; int found = 0;
    if (getifaddrs(&all)) return 0;
    for (struct ifaddrs *i = all; i && !found; i = i->ifa_next)
        if (i->ifa_addr && i->ifa_addr->sa_family == AF_INET && (i->ifa_flags & IFF_UP) && !(i->ifa_flags & IFF_LOOPBACK)) {
            *ip = ((struct sockaddr_in *)i->ifa_addr)->sin_addr;
            if (mask) *mask = i->ifa_netmask ? ((struct sockaddr_in *)i->ifa_netmask)->sin_addr : (struct in_addr){htonl(0xffffff00)};
            found = 1;
        }
    freeifaddrs(all);
    if (!found) { ip->s_addr = htonl(INADDR_LOOPBACK); if (mask) mask->s_addr = htonl(0xff000000); }
    return 1;
}
static uint32_t primary_ip(struct in_addr *out) {
    if (!online()) return NIFM_UNAVAILABLE;
    if (!out) runtime_fail("nifm address pointer");
    lan_address(out, NULL); return 0;
}
/* Gateway and DNS are not used by the game's online stack; the gateway's usual .1 stands in for both. */
static uint32_t ip_config(struct in_addr *ip, struct in_addr *mask, struct in_addr *gateway, struct in_addr *dns1, struct in_addr *dns2) {
    if (!online()) return NIFM_UNAVAILABLE;
    if (!ip || !mask || !gateway || !dns1 || !dns2) runtime_fail("nifm ip config pointers");
    lan_address(ip, mask);
    gateway->s_addr = (ip->s_addr & mask->s_addr) | htonl(1); *dns1 = *dns2 = *gateway; return 0;
}

/* ---------- nsd (section 4) ---------- */
static uint32_t resolve_ex(char *out, const char *in) {   /* Fqdn = char[256]; "%" -> "lp1" */
    if (!out || !in) runtime_fail("nsd ResolveEx pointers");
    char tmp[256]; size_t o = 0;
    for (size_t i = 0; i < 256 && in[i]; i++) {
        const char *put = in[i] == '%' ? "lp1" : (char[]){in[i], 0};
        size_t n = strlen(put); if (o + n >= sizeof tmp) return RESULT(141, 2);
        memcpy(tmp + o, put, n); o += n;
    }
    tmp[o] = 0; memcpy(out, tmp, o + 1); return 0;
}
/* OOF_NAT_ALIAS=1 (diagnostics, two instances on one Mac): the game sees nncs1 / nncs2 at 192.0.2.1 / 192.0.2.2 (TEST-NET-1)
   and sendto / recvfrom translate. Pia drops every datagram from a NAT check server's IP, port aside (main+0xcfc94 with
   main+0xb2820), and on one Mac those servers hold the only two addresses the instances can use. */
#define NAT_ALIAS_IP 0xc0000201u
static int nat_alias(void) { const char *e = getenv("OOF_NAT_ALIAS"); return e && *e && strcmp(e, "0"); }
static void nat_alias_to(struct sockaddr_in *to) {   /* virtual NAT check address -> the real one */
    struct in_addr check[2]; uint32_t i = ntohl(to->sin_addr.s_addr) - NAT_ALIAS_IP;
    if (nat_alias() && i < 2 && runtime_oof_nat_check(check)) to->sin_addr = check[i];
}
static void virt_to(struct sockaddr_in *to) {   /* a virtual peer -> this Mac */
    if (virt_index() && (ntohl(to->sin_addr.s_addr) & 0xffffff00u) == VIRT_NET) to->sin_addr.s_addr = htonl(INADDR_LOOPBACK);
}
static void virt_from(struct sockaddr *from, void *buf, ssize_t n) {   /* this Mac's instances -> their virtual addresses */
    struct sockaddr_in *in = (struct sockaddr_in *)from;
    if (!virt_index() || !from || from->sa_family != AF_INET) return;
    unsigned port = ntohs(in->sin_port); uint32_t ip = ntohl(in->sin_addr.s_addr);
    if ((port == 10025 || port == 10125) && n == 16) {   /* NAT check answer: u32 type, port, seen IPv4, address */
        uint32_t me = htonl(VIRT_NET + (uint32_t)virt_index()); memcpy((char *)buf + 8, &me, 4); return;
    }
    if (!virt_of_port(port)) return;
    static uint32_t mine[8]; static int count = -1;   /* this Mac's IPv4 addresses, read once */
    if (count < 0) { struct ifaddrs *all = NULL; count = 0; if (!getifaddrs(&all)) { for (struct ifaddrs *i = all; i && count < 8; i = i->ifa_next)
        if (i->ifa_addr && i->ifa_addr->sa_family == AF_INET) mine[count++] = ntohl(((struct sockaddr_in *)i->ifa_addr)->sin_addr.s_addr);
        freeifaddrs(all); } }
    for (int i = 0; i < count; i++) if (mine[i] == ip) { in->sin_addr.s_addr = htonl(VIRT_NET + (uint32_t)virt_of_port(port)); return; }
}
static void nat_alias_from(struct sockaddr *from) {   /* a NAT check server's answer (ports 10025 / 10125) -> its virtual address */
    struct in_addr check[2]; struct sockaddr_in *in = (struct sockaddr_in *)from;
    if (!from || from->sa_family != AF_INET || !nat_alias() || !runtime_oof_nat_check(check)) return;
    if (ntohs(in->sin_port) != 10025 && ntohs(in->sin_port) != 10125) return;
    for (uint32_t i = 0; i < 2; i++) if (in->sin_addr.s_addr == check[i].s_addr) { in->sin_addr.s_addr = htonl(NAT_ALIAS_IP + i); return; }
}
/* The names the standard redirects (section 4) and their address: the server's, or for nncs1 / nncs2 the two NAT check
   addresses it announces (oof_client.m). The socket resolver fills in "%" itself, as the console's does: Pia passes
   "nncs1-%.n.n.srv.nintendo.net" straight to GetAddrInfo (main+0xba850). */
static int oof_name(const char *n, struct in_addr *ip) {
    char e[256];
    if (!n || resolve_ex(e, n)) return 0;
    int nat = !strcmp(e, "nncs1-lp1.n.n.srv.nintendo.net") ? 1 : !strcmp(e, "nncs2-lp1.n.n.srv.nintendo.net") ? 2 : 0;
    if (!nat && strcmp(e, "g23380901-lp1.s.n.srv.nintendo.net")) return 0;
    struct in_addr check[2];
    *ip = nat && runtime_oof_nat_check(check) ? check[nat - 1] : server_ip;
    if (nat && nat_alias() && runtime_oof_nat_check(check)) ip->s_addr = htonl(NAT_ALIAS_IP + (uint32_t)nat - 1);
    return 1;
}

/* ---------- nn::socket (sections 4 and 6) ---------- */
/* OOF_NET_TRACE=1: every socket / TLS call with its arguments and result (diagnostics). */
static int trace_on(void) { static int on = -1; if (on < 0) { const char *e = getenv("OOF_NET_TRACE"); on = e && *e && strcmp(e, "0"); } return on; }
#define TRACE(...) do { if (trace_on()) fprintf(stderr, "[oof-trace] " __VA_ARGS__); } while (0)
static __thread int last_errno;   /* Horizon (Linux) numbering */
static int sw_errno(int e) {   /* 1-34 are the same except EDEADLK / EAGAIN */
    switch (e) {
    case EDEADLK: return 35; case EAGAIN: return 11; case EINPROGRESS: return 115; case EALREADY: return 114;
    case ENOTSOCK: return 88; case EDESTADDRREQ: return 89; case EMSGSIZE: return 90; case EPROTOTYPE: return 91;
    case ENOPROTOOPT: return 92; case EPROTONOSUPPORT: return 93; case ESOCKTNOSUPPORT: return 94; case EOPNOTSUPP: return 95;
    case EPFNOSUPPORT: return 96; case EAFNOSUPPORT: return 97; case EADDRINUSE: return 98; case EADDRNOTAVAIL: return 99;
    case ENETDOWN: return 100; case ENETUNREACH: return 101; case ENETRESET: return 102; case ECONNABORTED: return 103;
    case ECONNRESET: return 104; case ENOBUFS: return 105; case EISCONN: return 106; case ENOTCONN: return 107;
    case ESHUTDOWN: return 108; case ETOOMANYREFS: return 109; case ETIMEDOUT: return 110; case ECONNREFUSED: return 111;
    case ELOOP: return 40; case ENAMETOOLONG: return 36; case EHOSTDOWN: return 112; case EHOSTUNREACH: return 113;
    case ENOTEMPTY: return 39;
    default: return e;
    }
}
static int fail(void) { last_errno = sw_errno(errno); return -1; }
static int done(int r) { if (r < 0) last_errno = sw_errno(errno); return r; }
static ssize_t done_size(ssize_t r) { if (r < 0) last_errno = sw_errno(errno); return r; }
static void sock_set_errno(int e) { last_errno = e; }
static int sock_getpeername(int fd, struct sockaddr *addr, socklen_t *len) { return done(getpeername(fd, addr, len)); }
/* GetHostByName (NEX's resolver): only the standard's names, as for GetAddrInfo. hostent has the same layout in FreeBSD and
   macOS; the answer lives in thread storage until the thread's next call, like the C library's. */
static struct hostent *sock_gethostbyname(const char *name, const void *options, unsigned long count) {
    (void)options; (void)count;
    static __thread struct hostent h; static __thread struct in_addr addr; static __thread char *list[2]; static __thread char host[256];
    TRACE("GetHostByName(%s)\n", name ? name : "(null)");
    if (!online() || !oof_name(name, &addr)) { fprintf(stderr, "[oof-net] name %s not resolved (%s)\n", name ? name : "(null)", online() ? "not an ssbu-oof name" : "offline"); last_errno = ENOENT; return NULL; }
    snprintf(host, sizeof host, "%s", name); list[0] = (char *)&addr; list[1] = NULL;
    h = (struct hostent){.h_name = host, .h_aliases = &list[1], .h_addrtype = AF_INET, .h_length = 4, .h_addr_list = list};
    fprintf(stderr, "[oof-net] %s -> %s\n", name, inet_ntoa(addr));
    return &h;
}
/* Resolver options (timeouts, cancel handles): accepted and not needed by the host resolver. */
static int resolver_set_option(const void *option) { (void)option; TRACE("ResolverSetOption\n"); return 0; }
static int resolver_get_option(void *option, int key) { (void)option; TRACE("ResolverGetOption(key %d): not available\n", key); last_errno = EINVAL; return -1; }
static int sock_errno(void) { TRACE("GetLastErrno -> %d\n", last_errno); return last_errno; }
static int sock_initialize(void *mem, size_t size, size_t alloc, int concurrency) { (void)mem; (void)size; (void)alloc; (void)concurrency; return 0; }
static int sock_socket(int family, int type, int protocol) {
    /* Offline, sockets exist only for a local-play network (ldn_host.c): Pia talks to the other Macs by address; names still do not resolve. */
    if (!online() && !runtime_ldn_active()) { errno = ENETDOWN; return fail(); }
    int s = socket(family, type & ~(SW_SOCK_NONBLOCK | SW_SOCK_CLOEXEC), protocol);
    if (s < 0) return fail();
    int one = 1; setsockopt(s, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);   /* a closed peer is an error, not a signal */
    if (type & SW_SOCK_NONBLOCK) fcntl(s, F_SETFL, fcntl(s, F_GETFL) | O_NONBLOCK);
    TRACE("Socket(%d, %#x, %d) -> %d\n", family, type, protocol, s);
    return s;
}
/* "a.b.c.d:port" of an IPv4 address for traces; "-" otherwise. */
static const char *sa_text(const struct sockaddr *a, char out[32]) {
    if (!a || a->sa_family != AF_INET) return "-";
    const struct sockaddr_in *in = (const struct sockaddr_in *)a; char ip[INET_ADDRSTRLEN];
    snprintf(out, 32, "%s:%u", inet_ntop(AF_INET, &in->sin_addr, ip, sizeof ip), ntohs(in->sin_port)); return out;
}
/* The first bytes of a datagram as hex, for traces (Pia's NAT check messages are 16-76 bytes). */
enum { HEX_MAX = 320 };   /* bytes of each datagram a trace shows: enough for a Pia connection request */
static const char *hex_text(const void *p, ssize_t n, char out[2 * HEX_MAX + 1]) {
    ssize_t m = n < 0 ? 0 : n > HEX_MAX ? HEX_MAX : n;
    for (ssize_t i = 0; i < m; i++) snprintf(out + 2 * i, 3, "%02x", ((const unsigned char *)p)[i]);
    out[2 * m] = 0; return out;
}
int runtime_module_offset(uint64_t pc, const char **name, uint64_t *off);
/* " main+0x…" for each guest frame record above the host function the game called. The first records are the host's (this
   function, then the stack switch of hle_stack.S, whose record holds the guest's frame pointer and return address). */
static const char *guest_callers(char out[256]) {
    size_t o = 0; const char *module; uint64_t off; out[0] = 0;
    uintptr_t *fp = __builtin_frame_address(0);
    for (int i = 0; i < 16 && fp && !((uintptr_t)fp & 15) && o < 256 - 48; i++, fp = (uintptr_t *)fp[0]) {
        if (runtime_module_offset(fp[1], &module, &off)) o += (size_t)snprintf(out + o, 256 - o, " %s+0x%llx", module, (unsigned long long)off);
        else if (o || i > 3) break;
    }
    return o ? out : " ?";
}
static int blocking(int fd, int flags) { return !(flags & MSG_DONTWAIT) && !(fcntl(fd, F_GETFL) & O_NONBLOCK); }
/* A TCP connection to the server on 443 goes to the server's own port (section 4). */
static int sock_connect(int fd, const struct sockaddr *addr, socklen_t len) {
    struct sockaddr_in to;
    if (addr && len >= sizeof to && addr->sa_family == AF_INET) {
        memcpy(&to, addr, sizeof to);
        if (online() > 0 && to.sin_addr.s_addr == server_ip.s_addr && ntohs(to.sin_port) == 443) to.sin_port = htons(server_port);
        to.sin_len = sizeof to; addr = (const struct sockaddr *)&to; len = sizeof to;
        /* OOF_UDP_SOURCE (see sock_bind) also sources the server connection: Pia takes its public address from the URL
           Register returns, which the server builds from this connection's peer address */
        struct sockaddr_in src = {.sin_len = sizeof src, .sin_family = AF_INET}; const char *source = getenv("OOF_UDP_SOURCE");
        if (source && inet_pton(AF_INET, source, &src.sin_addr) == 1 && bind(fd, (struct sockaddr *)&src, sizeof src))
            fprintf(stderr, "[oof-net] OOF_UDP_SOURCE %s: TCP bind failed: %s\n", source, strerror(errno));
    }
    int away = blocking(fd, 0) ? runtime_core_away() : 0, r = connect(fd, addr, len), e = errno;
    if (away) runtime_core_back(&away);
    TRACE("Connect(%d, %s:%u) -> %d errno %d\n", fd, addr && addr->sa_family == AF_INET ? inet_ntoa(((const struct sockaddr_in *)addr)->sin_addr) : "?",
          addr && addr->sa_family == AF_INET ? ntohs(((const struct sockaddr_in *)addr)->sin_port) : 0, r, r < 0 ? e : 0);
    errno = e; return done(r);
}
/* UDP: the same port when it is free (the game advertises it), else any port; GetSockName reports the real one. */
static int sock_bind(int fd, const struct sockaddr *addr, socklen_t len) {
    /* OOF_UDP_SOURCE=<IPv4> (diagnostics, two instances on one Mac): UDP sockets bound to any address are bound to this one, so
       every NAT check probe leaves from the same address, as behind a home router. Without it, probes to a loopback and a LAN
       server address leave from two addresses and Pia classifies the NAT as address-dependent. */
    struct sockaddr_in fixed; const char *source = getenv("OOF_UDP_SOURCE"); int type = 0; socklen_t tl = sizeof type;
    if (source && addr && addr->sa_family == AF_INET && len >= sizeof fixed && !getsockopt(fd, SOL_SOCKET, SO_TYPE, &type, &tl) && type == SOCK_DGRAM) {
        memcpy(&fixed, addr, sizeof fixed);
        if (fixed.sin_addr.s_addr == htonl(INADDR_ANY) && inet_pton(AF_INET, source, &fixed.sin_addr) == 1) { fixed.sin_len = sizeof fixed; addr = (const struct sockaddr *)&fixed; }
    }
    struct sockaddr_in in_range;
    if (virt_index() && addr && addr->sa_family == AF_INET && len >= sizeof in_range && !getsockopt(fd, SOL_SOCKET, SO_TYPE, &type, &tl) && type == SOCK_DGRAM) {
        memcpy(&in_range, addr, sizeof in_range); in_range.sin_len = sizeof in_range; in_range.sin_addr.s_addr = htonl(INADDR_ANY);
        unsigned base = VIRT_PORT0 + 1000u * (unsigned)(virt_index() - 1), first = ntohs(in_range.sin_port) % 1000;
        int r = -1;
        for (unsigned k = 0; k < 1000 && r < 0; k++) { in_range.sin_port = htons((uint16_t)(base + (first + k) % 1000)); r = bind(fd, (struct sockaddr *)&in_range, sizeof in_range); }
        TRACE("Bind(%d, virtual instance %d) -> %d, port %u\n", fd, virt_index(), r, ntohs(in_range.sin_port));
        return done(r);
    }
    int r = bind(fd, addr, len);
    if (r < 0 && errno == EADDRINUSE && addr && addr->sa_family == AF_INET && len >= sizeof(struct sockaddr_in)) {
        int type = 0; socklen_t tl = sizeof type; getsockopt(fd, SOL_SOCKET, SO_TYPE, &type, &tl);
        if (type == SOCK_DGRAM) {
            struct sockaddr_in any; memcpy(&any, addr, sizeof any); any.sin_port = 0; any.sin_len = sizeof any;
            fprintf(stderr, "[oof-net] UDP port %u is taken (another game instance?): bound to a free port\n", ntohs(((const struct sockaddr_in *)addr)->sin_port));
            r = bind(fd, (const struct sockaddr *)&any, sizeof any);
        }
    }
    int e = errno;
    if (trace_on()) { struct sockaddr_storage got; socklen_t gl = sizeof got; char a[32], b[32];
        TRACE("Bind(%d, %s) -> %d errno %d, bound %s\n", fd, sa_text(addr, a), r, r < 0 ? e : 0, getsockname(fd, (struct sockaddr *)&got, &gl) ? "-" : sa_text((struct sockaddr *)&got, b)); }
    errno = e; return done(r);
}
static int sock_listen(int fd, int backlog) { return done(listen(fd, backlog)); }
static int sock_accept(int fd, struct sockaddr *addr, socklen_t *len) {
    int away = blocking(fd, 0) ? runtime_core_away() : 0, r = accept(fd, addr, len), e = errno;
    if (away) runtime_core_back(&away);
    errno = e; return done(r);
}
static ssize_t sock_recv(int fd, void *buf, size_t n, int flags) {
    int away = blocking(fd, flags) ? runtime_core_away() : 0; ssize_t r = recv(fd, buf, n, flags); int e = errno;
    if (away) runtime_core_back(&away);
    errno = e; return done_size(r);
}
static ssize_t sock_recvfrom(int fd, void *buf, size_t n, int flags, struct sockaddr *from, socklen_t *len) {
    int away = blocking(fd, flags) ? runtime_core_away() : 0; ssize_t r = recvfrom(fd, buf, n, flags, from, len); int e = errno;
    if (away) runtime_core_back(&away);
    if (r >= 0) { nat_alias_from(from); virt_from(from, buf, r); }
    if (r >= 0 || e != EAGAIN) { char a[32], h[2 * HEX_MAX + 1]; TRACE("RecvFrom(%d, %zu) -> %zd errno %d from %s %s\n", fd, n, r, r < 0 ? e : 0, r >= 0 ? sa_text(from, a) : "-", hex_text(buf, r, h)); }   /* polling misses stay quiet */
    errno = e; return done_size(r);
}
static ssize_t sock_send(int fd, const void *buf, size_t n, int flags) {
    int away = blocking(fd, flags) ? runtime_core_away() : 0; ssize_t r = send(fd, buf, n, flags); int e = errno;
    if (away) runtime_core_back(&away);
    errno = e; return done_size(r);
}
static ssize_t sock_sendto(int fd, const void *buf, size_t n, int flags, const struct sockaddr *to, socklen_t len) {
    struct sockaddr_in real;
    if (to && to->sa_family == AF_INET && len >= sizeof real) { memcpy(&real, to, sizeof real); nat_alias_to(&real); virt_to(&real); to = (const struct sockaddr *)&real; len = sizeof real; }
    ssize_t r = sendto(fd, buf, n, flags, to, len); int e = errno;   /* UDP: never waits */
    char a[32], h[2 * HEX_MAX + 1], c[256]; TRACE("SendTo(%d, %zu bytes, %s) -> %zd errno %d %s from%s\n", fd, n, sa_text(to, a), r, r < 0 ? e : 0, hex_text(buf, (ssize_t)n, h), guest_callers(c));
    errno = e; return done_size(r);
}
static int sock_poll(struct pollfd *fds, unsigned long n, int timeout) {
    int away = timeout ? runtime_core_away() : 0, r = poll(fds, (nfds_t)n, timeout), e = errno;
    if (away) runtime_core_back(&away);
    if (trace_on()) { char b[256]; size_t o = 0; for (unsigned long i = 0; i < n && i < 8 && o < sizeof b - 32; i++) o += (size_t)snprintf(b + o, sizeof b - o, " fd%d ev%#x re%#x", fds[i].fd, fds[i].events, fds[i].revents);
        TRACE("Poll(%lu, %d ms)%s -> %d errno %d\n", n, timeout, n ? b : "", r, r < 0 ? e : 0); }
    errno = e; return done(r);
}
static int sock_fcntl(int fd, int cmd, int arg) {   /* variadic in the SDK: the guest passes arg in a register */
    TRACE("Fcntl(%d, cmd %d, %#x)\n", fd, cmd, arg);
    if (cmd == F_GETFL) return done(fcntl(fd, F_GETFL));
    if (cmd == F_SETFL) return done(fcntl(fd, F_SETFL, arg & O_NONBLOCK ? fcntl(fd, F_GETFL) | O_NONBLOCK : fcntl(fd, F_GETFL) & ~O_NONBLOCK));
    errno = EINVAL; return fail();
}
static int sock_setsockopt(int fd, int level, int name, const void *value, socklen_t len) {
    int r = setsockopt(fd, level, name, value, len);
    TRACE("SetSockOpt(%d, level %#x, option %#x, %u bytes) -> %d errno %d\n", fd, level, name, len, r, r < 0 ? errno : 0);
    if (r < 0) { static int reported; if (reported++ < 8) fprintf(stderr, "[oof-net] setsockopt level %#x option %#x: %s (ignored)\n", level, name, strerror(errno)); return 0; }
    return 0;
}
static int sock_getsockopt(int fd, int level, int name, void *value, socklen_t *len) { return done(getsockopt(fd, level, name, value, len)); }
static int sock_getsockname(int fd, struct sockaddr *addr, socklen_t *len) {
    int r = getsockname(fd, addr, len), e = errno; char a[32];
    TRACE("GetSockName(%d) -> %d %s\n", fd, r, r ? "-" : sa_text(addr, a));
    errno = e; return done(r);
}
static int sock_shutdown(int fd, int how) { return done(shutdown(fd, how)); }
static void ssl_release_fd(int fd);
static int sock_close(int fd) { TRACE("Close(%d)\n", fd); ssl_release_fd(fd); return done(close(fd)); }
static int sock_finalize(void) { return 0; }
/* Only the standard's names resolve, to the server; numeric addresses pass. Nothing reaches a Nintendo host. */
static int sock_getaddrinfo(const char *node, const char *service, const struct addrinfo *hints, struct addrinfo **res) {
    TRACE("GetAddrInfo(%s, %s, family %d socktype %d flags %#x)\n", node ? node : "(null)", service ? service : "(null)", hints ? hints->ai_family : -1, hints ? hints->ai_socktype : -1, hints ? hints->ai_flags : 0);
    struct addrinfo h = {0};
    if (hints) { h = *hints; h.ai_canonname = NULL; h.ai_addr = NULL; h.ai_next = NULL; if (h.ai_flags & SW_AI_NUMERICSERV) h.ai_flags = (h.ai_flags & ~SW_AI_NUMERICSERV) | AI_NUMERICSERV; }
    if (!res) return EAI_FAIL;
    struct in_addr numeric;
    if (node && inet_aton(node, &numeric)) return getaddrinfo(node, service, &h, res);
    struct in_addr to;
    if (!online() || !oof_name(node, &to)) {
        fprintf(stderr, "[oof-net] name %s not resolved (%s)\n", node ? node : "(null)", online() ? "not an ssbu-oof name" : "offline");
        return EAI_NONAME;
    }
    char ip[INET_ADDRSTRLEN]; inet_ntop(AF_INET, &to, ip, sizeof ip);
    h.ai_flags |= AI_NUMERICHOST; if (!h.ai_family) h.ai_family = AF_INET;
    fprintf(stderr, "[oof-net] %s -> %s\n", node, ip);
    return getaddrinfo(ip, service, &h, res);
}
static void sock_freeaddrinfo(struct addrinfo *ai) { if (ai) freeaddrinfo(ai); }
/* nn::socket::AddrInfo is not the BSD addrinfo: the game reads ai_addr at +0x18 and ai_next at +0x28
   (main+0xba8d0 / +0xba8e4), while the plain addrinfo overload reads ai_addr at +0x20 (main+0x1bed9c, as macOS). */
typedef struct NnAddrInfo { int32_t flags, family, socktype, protocol; uint32_t addrlen, pad; struct sockaddr *addr; char *canonname; struct NnAddrInfo *next; } NnAddrInfo;
_Static_assert(offsetof(NnAddrInfo, addr) == 0x18 && offsetof(NnAddrInfo, next) == 0x28 && sizeof(NnAddrInfo) == 0x30, "nn::socket::AddrInfo");
static int sock_getaddrinfo_nn(const char *node, const char *service, const struct addrinfo *hints, NnAddrInfo **res) {
    struct addrinfo *list = NULL; int r = sock_getaddrinfo(node, service, hints, &list);
    if (r || !res) return r;
    NnAddrInfo *head = NULL, **tail = &head;
    for (struct addrinfo *a = list; a; a = a->ai_next) {
        size_t name = a->ai_canonname ? strlen(a->ai_canonname) + 1 : 0;
        NnAddrInfo *n = calloc(1, sizeof *n + a->ai_addrlen + name);
        if (!n) runtime_fail("nn::socket::AddrInfo allocation");
        *n = (NnAddrInfo){a->ai_flags, a->ai_family, a->ai_socktype, a->ai_protocol, a->ai_addrlen, 0, (struct sockaddr *)(n + 1), NULL, NULL};
        memcpy(n->addr, a->ai_addr, a->ai_addrlen);
        if (name) { n->canonname = (char *)(n + 1) + a->ai_addrlen; memcpy(n->canonname, a->ai_canonname, name); }
        *tail = n; tail = &n->next;
    }
    freeaddrinfo(list); *res = head; return 0;
}
static void sock_freeaddrinfo_nn(NnAddrInfo *ai) { while (ai) { NnAddrInfo *next = ai->next; free(ai); ai = next; } }
static const char *sock_gai_strerror(int e) { return gai_strerror(e); }
static uint32_t sock_htonl(uint32_t v) { return htonl(v); }
static uint16_t sock_htons(uint16_t v) { return htons(v); }
static int sock_inet_aton(const char *s, struct in_addr *out) { return inet_aton(s, out); }
static char *sock_inet_ntoa(struct in_addr a) { static __thread char buf[INET_ADDRSTRLEN]; return strcpy(buf, inet_ntoa(a)); }
static const char *sock_inet_ntop(int family, const void *src, char *dst, socklen_t size) { return inet_ntop(family, src, dst, size); }
static int sock_inet_pton(int family, const char *src, void *dst) { return inet_pton(family, src, dst); }

/* ---------- nn::ssl C API (section 5) ---------- */
/* Each nnsslConnection is the guest's own object; the host keeps its socket and I/O mode in a small table keyed by
   that pointer. Connection::Destroy closes the socket in the SDK too (nn::socket::Close). */
typedef struct { const void *guest; int fd, nonblocking; SSLContextRef tls; } SslConn;
static SslConn conns[64];
static pthread_mutex_t ssl_mu = PTHREAD_MUTEX_INITIALIZER;
static SslConn *conn_of(const void *guest, int create, const char *what) {   /* ssl_mu held */
    SslConn *empty = NULL;
    for (int i = 0; i < 64; i++) { if (conns[i].guest == guest) return &conns[i]; if (!conns[i].guest && !empty) empty = &conns[i]; }
    if (!create) { fprintf(stderr, "[oof-net] %s on nn::ssl connection %p that was never created\n", what, guest); runtime_fail("unknown nn::ssl connection"); }
    if (!empty) runtime_fail("more than 64 nn::ssl connections");
    *empty = (SslConn){.guest = guest, .fd = -1}; return empty;
}
static void ssl_release_fd(int fd) {   /* the game closed the socket itself: the connection no longer owns it */
    pthread_mutex_lock(&ssl_mu);
    for (int i = 0; i < 64; i++) if (conns[i].guest && conns[i].fd == fd) conns[i].fd = -1;
    pthread_mutex_unlock(&ssl_mu);
}
static uint32_t ssl_context_create(void *ctx, int version) { (void)ctx; (void)version; return 0; }
static uint32_t ssl_context_destroy(void *ctx) { (void)ctx; return 0; }
static uint32_t ssl_context_id(void *ctx, uint64_t *out) { if (!out) runtime_fail("nn::ssl context id pointer"); *out = (uint64_t)(uintptr_t)ctx; return 0; }
/* The imported Nintendo PKI is not used for the ssbu-oof server (section 5). */
static uint32_t ssl_import_pki(void *ctx, uint64_t *out, const char *data, uint32_t size, int format) {
    (void)ctx; (void)data; (void)size; (void)format; if (out) *out = 1; return 0;
}
static uint32_t ssl_conn_create(void *c, void *ctx) {
    (void)ctx; if (!c) return RESULT(123, 113);
    pthread_mutex_lock(&ssl_mu); conn_of(c, 1, "Create"); fprintf(stderr, "[oof-net] nn::ssl connection %p created\n", c); pthread_mutex_unlock(&ssl_mu); return 0;
}
static uint32_t ssl_conn_destroy(void *c) {   /* the game also destroys connections it never created (cleanup): nothing to do */
    pthread_mutex_lock(&ssl_mu);
    SslConn *k = NULL; for (int i = 0; i < 64 && !k; i++) if (conns[i].guest == c) k = &conns[i];
    if (!k) { pthread_mutex_unlock(&ssl_mu); return 0; }
    int fd = k->fd; SSLContextRef tls = k->tls; *k = (SslConn){0}; pthread_mutex_unlock(&ssl_mu);
    if (tls) { SSLClose(tls); CFRelease(tls); }
    if (fd >= 0) close(fd);
    return 0;
}
static uint32_t ssl_set_socket(void *c, int fd) {
    TRACE("ssl SetSocketDescriptor(%p, %d)\n", c, fd); pthread_mutex_lock(&ssl_mu); conn_of(c, 0, "SetSocketDescriptor")->fd = fd; pthread_mutex_unlock(&ssl_mu); return 0; }
static uint32_t ssl_set_host(void *c, const char *name, uint32_t len) {
    (void)c; fprintf(stderr, "[oof-net] TLS connection to %.*s\n", (int)(len && len < 256 ? len : 0), name ? name : ""); return 0;
}
static uint32_t ssl_set_io_mode(void *c, int mode) {   /* IoMode: 1 blocking, 2 non-blocking */
    pthread_mutex_lock(&ssl_mu); conn_of(c, 0, "SetIoMode")->nonblocking = mode == 2; pthread_mutex_unlock(&ssl_mu); return 0;
}
static uint32_t ssl_set_ignored(void *c, int value, int extra) { (void)c; (void)value; (void)extra; return 0; }
/* OOF_CA: PEM certificates trusted for the server instead of the system store (standard section 1, oof.ca). */
static CFMutableArrayRef anchors;
static void anchors_load(void) {
    const char *path = getenv("OOF_CA"); FILE *f = path && *path ? fopen(path, "r") : NULL;
    if (path && *path && !f) { fprintf(stderr, "【红】[oof-net] OOF_CA %s: %s\n", path, strerror(errno)); return; }
    if (!f) return;
    anchors = CFArrayCreateMutable(NULL, 0, &kCFTypeArrayCallBacks);
    static const char b64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    char line[256]; unsigned char der[16384]; size_t n = 0; int in = 0; unsigned bits = 0, nbits = 0;
    while (fgets(line, sizeof line, f)) {
        if (strstr(line, "-----BEGIN CERTIFICATE-----")) { in = 1; n = 0; bits = nbits = 0; continue; }
        if (strstr(line, "-----END CERTIFICATE-----") && in) {
            CFDataRef d = CFDataCreate(NULL, der, (CFIndex)n); SecCertificateRef cert = SecCertificateCreateWithData(NULL, d); CFRelease(d);
            if (cert) { CFArrayAppendValue(anchors, cert); CFRelease(cert); } else fprintf(stderr, "【红】[oof-net] OOF_CA %s: unreadable certificate\n", path);
            in = 0; continue;
        }
        for (char *c = line; in && *c; c++) {
            const char *v = strchr(b64, *c); if (!v) continue;
            bits = bits << 6 | (unsigned)(v - b64); nbits += 6;
            if (nbits >= 8 && n < sizeof der) { nbits -= 8; der[n++] = (unsigned char)(bits >> nbits); }
        }
    }
    fclose(f);
    fprintf(stderr, "[oof-net] OOF_CA %s: %ld certificate(s) trusted for the server\n", path, (long)CFArrayGetCount(anchors));
}
static CFArrayRef oof_anchors(void) {
    static pthread_once_t once = PTHREAD_ONCE_INIT; pthread_once(&once, anchors_load);
    return anchors && CFArrayGetCount(anchors) ? anchors : NULL;
}
/* The server's certificate chain, already bound to its host name by the TLS stack; shared with the token client. */
int runtime_oof_trust_ok(void *trust) {
    CFArrayRef anchors = oof_anchors(); CFErrorRef error = NULL;
    if (anchors) { SecTrustSetAnchorCertificates(trust, anchors); SecTrustSetAnchorCertificatesOnly(trust, true); }
    bool ok = SecTrustEvaluateWithError(trust, &error);
    if (!ok) {
        char why[256] = "?"; CFStringRef d = error ? CFErrorCopyDescription(error) : NULL;
        if (d) { CFStringGetCString(d, why, sizeof why, kCFStringEncodingUTF8); CFRelease(d); }
        fprintf(stderr, "【红】[oof-net] the server's certificate is not trusted%s: %s\n", anchors ? " (OOF_CA)" : "", why);
    }
    if (error) CFRelease(error);
    return ok;
}
/* SecureTransport I/O on the game's socket, never waiting: the callers wait when the game's I/O mode is blocking. */
static OSStatus tls_read(SSLConnectionRef c, void *data, size_t *len) {
    size_t want = *len, got = 0; int fd = (int)(intptr_t)c;
    while (got < want) {
        ssize_t r = recv(fd, (char *)data + got, want - got, MSG_DONTWAIT);
        if (r > 0) { got += (size_t)r; continue; }
        *len = got;
        return r == 0 ? errSSLClosedGraceful : errno == EAGAIN ? errSSLWouldBlock : errSSLClosedAbort;
    }
    return noErr;
}
static OSStatus tls_write(SSLConnectionRef c, const void *data, size_t *len) {
    size_t want = *len, put = 0; int fd = (int)(intptr_t)c;
    while (put < want) {
        ssize_t r = send(fd, (const char *)data + put, want - put, MSG_DONTWAIT);
        if (r > 0) { put += (size_t)r; continue; }
        *len = put;
        return r < 0 && errno == EAGAIN ? errSSLWouldBlock : errSSLClosedAbort;
    }
    return noErr;
}
static int tls_wait(int fd, short events, int ms) {   /* releases the virtual core while it waits */
    struct pollfd p = {.fd = fd, .events = events}; int away = runtime_core_away(), r = poll(&p, 1, ms);
    runtime_core_back(&away); return r;
}
static uint32_t ssl_handshake(void *c, uint32_t *cert_size, uint32_t *cert_count) {
    if (cert_size) *cert_size = 0;
    if (cert_count) *cert_count = 0;
    if (tls_off()) return 0;
    int fd; pthread_mutex_lock(&ssl_mu); fd = conn_of(c, 0, "DoHandshake")->fd; pthread_mutex_unlock(&ssl_mu);
    if (fd < 0 || online() <= 0) return SSL_NO_TLS;
    SSLContextRef tls = SSLCreateContext(NULL, kSSLClientSide, kSSLStreamType);
    if (!tls) runtime_fail("SSLCreateContext");
    SSLSetIOFuncs(tls, tls_read, tls_write); SSLSetConnection(tls, (SSLConnectionRef)(intptr_t)fd);
    SSLSetPeerDomainName(tls, server_host, strlen(server_host));   /* the server, not the Nintendo name the game asked for */
    SSLSetProtocolVersionMin(tls, kTLSProtocol12); SSLSetSessionOption(tls, kSSLSessionOptionBreakOnServerAuth, true);
    OSStatus st; int waited = 0;
    while ((st = SSLHandshake(tls)) != noErr) {
        if (st == errSSLServerAuthCompleted) {
            SecTrustRef trust = NULL; int ok = !SSLCopyPeerTrust(tls, &trust) && trust && runtime_oof_trust_ok(trust);
            if (trust) CFRelease(trust);
            if (ok) continue;
            st = errSSLXCertChainInvalid; break;
        }
        if (st != errSSLWouldBlock || waited >= 15000) break;
        tls_wait(fd, POLLIN, 100); waited += 100;
    }
    TRACE("ssl DoHandshake with %s -> %d\n", server_host, (int)st);
    if (st != noErr) {
        fprintf(stderr, "【红】[oof-net] TLS handshake with %s failed (%d)%s\n", server_host, (int)st, waited >= 15000 ? ": timed out" : "");
        CFRelease(tls); return SSL_NO_TLS;
    }
    pthread_mutex_lock(&ssl_mu); conn_of(c, 0, "DoHandshake")->tls = tls; pthread_mutex_unlock(&ssl_mu);
    return 0;
}
static uint32_t ssl_verify_error(void *c, uint32_t *out) { (void)c; if (out) *out = 0; return 0; }
static uint32_t ssl_conn_id(void *c, uint64_t *out) {
    TRACE("ssl GetConnectionId(%p)\n", c); if (!out) runtime_fail("nn::ssl connection id pointer"); *out = (uint64_t)(uintptr_t)c; return 0; }
static SSLContextRef conn_io(void *c, int *fd, int *flags) {
    pthread_mutex_lock(&ssl_mu); SslConn *k = conn_of(c, 0, "Read/Write/Pending"); *fd = k->fd; *flags = k->nonblocking ? MSG_DONTWAIT : 0;
    SSLContextRef tls = k->tls; pthread_mutex_unlock(&ssl_mu); return tls;
}
/* OOF_NET_TRACE: the RMC header in a whole small WebSocket frame (the client's are masked) carrying a PRUDP lite DATA
   packet (12-byte header + options); shortcut: frames split across reads or over 125 bytes are not decoded. */
static void trace_rmc(const char *what, const unsigned char *b, ssize_t n) {
    if (!trace_on() || n < 2 || (b[0] & 0x0f) != 2 || (b[1] & 0x7f) > 125) return;
    size_t len = b[1] & 0x7f, at = b[1] & 0x80 ? 6 : 2; unsigned char p[125];
    if ((size_t)n < at + len) return;
    for (size_t i = 0; i < len; i++) p[i] = b[at + i] ^ (at == 6 ? b[2 + i % 4] : 0);
    size_t r = 12 + (len > 1 ? p[1] : 0);
    if (len < r + 13 || p[0] != 0x80 || (p[8] & 0xf) != 2 || (p[8] & 0x10)) return;   /* DATA, not an ACK */
    const unsigned char *m = p + r; int request = m[4] & 0x80;
    if (request) TRACE("ssl %s: RMC request %u/%u call %u, %zu-byte body\n", what, m[4] & 0x7f, m[9] | m[10] << 8, m[5] | m[6] << 8, len - r - 13);
    else if (m[5]) TRACE("ssl %s: RMC response %u/%u call %u, %zu-byte body\n", what, m[4], (m[10] | m[11] << 8) & 0x7fff, m[6] | m[7] << 8, len - r - 14);
    else TRACE("ssl %s: RMC error %u code %#x\n", what, m[4], m[6] | m[7] << 8 | m[8] << 16 | (unsigned)m[9] << 24);
}
static uint32_t io_result(const char *what, ssize_t r, int *out) {
    TRACE("ssl %s -> %zd errno %d\n", what, r, r < 0 ? errno : 0);
    if (r >= 0) { if (out) *out = (int)r; return 0; }
    if (errno == EAGAIN) return SSL_WOULD_BLOCK;
    fprintf(stderr, "[oof-net] TLS connection I/O: %s\n", strerror(errno)); return SSL_IO_FAILED;
}
static ssize_t tls_recv(SSLContextRef tls, int fd, char *buf, size_t size, int nonblocking) {
    for (;;) {
        size_t got = 0; OSStatus st = SSLRead(tls, buf, size, &got);
        if (got) return (ssize_t)got;
        if (st == errSSLClosedGraceful || st == errSSLClosedNoNotify) return 0;
        if (st != errSSLWouldBlock) { errno = ECONNRESET; return -1; }
        if (nonblocking) { errno = EAGAIN; return -1; }
        tls_wait(fd, POLLIN, -1);
    }
}
static ssize_t tls_send(SSLContextRef tls, int fd, const char *buf, size_t size) {   /* whole messages: WebSocket frames */
    size_t done = 0;
    for (int waited = 0;;) {
        size_t n = 0; OSStatus st = SSLWrite(tls, buf + done, size - done, &n); done += n;
        if (st == noErr && done == size) return (ssize_t)size;
        if (st != errSSLWouldBlock || waited >= 15000) { errno = st == errSSLWouldBlock ? ETIMEDOUT : ECONNRESET; return -1; }
        tls_wait(fd, POLLOUT, 100); waited += 100;
    }
}
static uint32_t ssl_read(void *c, char *buf, int *out, uint32_t size) {
    int fd, flags; SSLContextRef tls = conn_io(c, &fd, &flags); if (fd < 0) return RESULT(123, 103);
    ssize_t r = tls ? tls_recv(tls, fd, buf, size, flags != 0) : sock_recv(fd, buf, size, flags); trace_rmc("Read", (const unsigned char *)buf, r);
    return io_result("Read", r, out);
}
static uint32_t ssl_write(void *c, const char *buf, int *out, uint32_t size) {
    int fd, flags; SSLContextRef tls = conn_io(c, &fd, &flags); if (fd < 0) return RESULT(123, 103);
    ssize_t r = tls ? tls_send(tls, fd, buf, size) : sock_send(fd, buf, size, flags); trace_rmc("Write", (const unsigned char *)buf, r);
    return io_result("Write", r, out);
}
/* Decrypted bytes waiting, else the raw bytes on the socket as a hint (a record may still be incomplete: Read then
   reports would-block, which the game handles). */
static uint32_t ssl_pending(void *c, int *out) {
    int fd, flags, n = 0; SSLContextRef tls = conn_io(c, &fd, &flags); (void)flags;
    if (!out) runtime_fail("nn::ssl pending pointer");
    size_t buffered = 0;
    if (tls && !SSLGetBufferedReadSize(tls, &buffered) && buffered) { *out = (int)buffered; return 0; }
    if (fd >= 0 && ioctl(fd, FIONREAD, &n) < 0) n = 0;
    *out = n; return 0;
}

/* ---------- err ---------- */
/* The error applet does not exist here: the code the console would show is logged (category = 2000 + module) and the
   game continues as if the player closed the message. The game's frame records say which code path raised it. */
typedef struct { uint32_t category, number; } ErrorCode;
static void show_error(ErrorCode code) {
    char from[256];
    fprintf(stderr, "【红】[oof-net] the game shows error %04u-%04u (returns to%s)\n", code.category, code.number, guest_callers(from));
}

/* ---------- pctl ---------- */
static bool free_communication(uint8_t show_dialog) { (void)show_dialog; return true; }   /* no parental restriction on the Mac; the SDK returns a bool (sdk+0x33a690 "cset w0, eq": true = allowed) */

void *runtime_net_override(const char *name) {
    static const struct { const char *name; void *fn; } entries[] = {
        {"_ZN2nn4nifm10InitializeEv", ok}, {"_ZN2nn4nifm18IsNetworkAvailableEv", network_available},
        {"_ZN2nn4nifm27SubmitNetworkRequestAndWaitEv", nifm_request}, {"_ZN2nn4nifm20SubmitNetworkRequestEv", nifm_request},
        {"_ZN2nn4nifm20CancelNetworkRequestEv", nop}, {"_ZN2nn4nifm22IsNetworkRequestOnHoldEv", request_on_hold},
        {"_ZN2nn4nifm26HandleNetworkRequestResultEv", request_result},
        {"_ZN2nn4nifm31HandleNetworkRequestErrorResultEv", request_error_result},
        {"_ZN2nn4nifm22GetCurrentProxySettingEPNS0_12ProxySettingE", proxy_setting},
        {"_ZN2nn4nifm26GetCurrentPrimaryIpAddressEPNS_6socket6InAddrE", primary_ip},
        {"_ZN2nn4nifm26GetCurrentPrimaryIpAddressEP7in_addr", primary_ip},
        {"_ZN2nn4nifm22GetCurrentIpConfigInfoEP7in_addrS2_S2_S2_S2_", ip_config},
        {"_ZN2nn3nsd9ResolveExEPNS0_4FqdnERKS1_", resolve_ex},
        {"_ZN2nn4pctl32CheckFreeCommunicationPermissionEb", free_communication},
        {"_ZN2nn3err9ShowErrorENS0_9ErrorCodeE", show_error},
        {"_ZN2nn6socket10InitializeEPvmmi", sock_initialize}, {"_ZN2nn6socket8FinalizeEv", sock_finalize},
        {"_ZN2nn6socket12GetLastErrnoEv", sock_errno},
        {"_ZN2nn6socket6SocketEiii", sock_socket}, {"_ZN2nn6socket6SocketENS0_6FamilyENS0_4TypeENS0_8ProtocolE", sock_socket},
        {"_ZN2nn6socket7ConnectEiPK8sockaddrj", sock_connect},
        {"_ZN2nn6socket4BindEiPK8sockaddrj", sock_bind}, {"_ZN2nn6socket4BindEiPKNS0_8SockAddrEj", sock_bind},
        {"_ZN2nn6socket6ListenEii", sock_listen}, {"_ZN2nn6socket6AcceptEiP8sockaddrPj", sock_accept},
        {"_ZN2nn6socket4RecvEiPvmi", sock_recv},
        {"_ZN2nn6socket8RecvFromEiPvmiP8sockaddrPj", sock_recvfrom}, {"_ZN2nn6socket8RecvFromEiPvmNS0_7MsgFlagEPNS0_8SockAddrEPj", sock_recvfrom},
        {"_ZN2nn6socket4SendEiPKvmi", sock_send},
        {"_ZN2nn6socket6SendToEiPKvmiPK8sockaddrj", sock_sendto}, {"_ZN2nn6socket6SendToEiPKvmNS0_7MsgFlagEPKNS0_8SockAddrEj", sock_sendto},
        {"_ZN2nn6socket4PollEP6pollfdmi", sock_poll}, {"_ZN2nn6socket5FcntlEiiz", sock_fcntl},
        {"_ZN2nn6socket10SetSockOptEiiiPKvj", sock_setsockopt}, {"_ZN2nn6socket10SetSockOptEiNS0_5LevelENS0_6OptionEPKvj", sock_setsockopt},
        {"_ZN2nn6socket10GetSockOptEiiiPvPj", sock_getsockopt}, {"_ZN2nn6socket11GetSockNameEiP8sockaddrPj", sock_getsockname},
        {"_ZN2nn6socket8ShutdownEii", sock_shutdown}, {"_ZN2nn6socket5CloseEi", sock_close},
        {"_ZN2nn6socket11GetAddrInfoEPKcS2_PK8addrinfoPPS3_", sock_getaddrinfo},
        {"_ZN2nn6socket11GetAddrInfoEPKcS2_PKNS0_8AddrInfoEPPS3_", sock_getaddrinfo_nn},
        {"_ZN2nn6socket12FreeAddrInfoEP8addrinfo", sock_freeaddrinfo}, {"_ZN2nn6socket12FreeAddrInfoEPNS0_8AddrInfoE", sock_freeaddrinfo_nn},
        {"_ZN2nn6socket11GAIStrErrorENS0_7AiErrnoE", sock_gai_strerror},
        /* the C API NEX uses (each wraps the C++ function of the same name in the SDK) */
        {"nnsocketSocket", sock_socket}, {"nnsocketConnect", sock_connect}, {"nnsocketBind", sock_bind}, {"nnsocketClose", sock_close},
        {"nnsocketFcntl", sock_fcntl}, {"nnsocketPoll", sock_poll}, {"nnsocketRecv", sock_recv}, {"nnsocketSend", sock_send},
        {"nnsocketGetSockOpt", sock_getsockopt}, {"nnsocketSetSockOpt", sock_setsockopt}, {"nnsocketGetSockName", sock_getsockname},
        {"nnsocketGetPeerName", sock_getpeername}, {"nnsocketGetLastErrno", sock_errno}, {"nnsocketSetLastErrno", sock_set_errno},
        {"nnsocketInetHtons", sock_htons}, {"nnsocketInetNtohs", sock_htons},
        {"nnsocketGetHostByNameWithOptions", sock_gethostbyname},
        {"nnsocketResolverGetOption", resolver_get_option}, {"nnsocketResolverSetOption", resolver_set_option},
        {"_ZN2nn6socket9InetHtonlEj", sock_htonl}, {"_ZN2nn6socket9InetNtohlEj", sock_htonl},
        {"_ZN2nn6socket9InetHtonsEt", sock_htons}, {"_ZN2nn6socket9InetNtohsEt", sock_htons},
        {"_ZN2nn6socket8InetAtonEPKcPNS0_6InAddrE", sock_inet_aton}, {"_ZN2nn6socket8InetNtoaE7in_addr", sock_inet_ntoa},
        {"_ZN2nn6socket8InetNtopEiPKvPcj", sock_inet_ntop}, {"_ZN2nn6socket8InetPtonEiPKcPv", sock_inet_pton},
        {"_ZN2nn3ssl10InitializeEv", ok}, {"_ZN2nn3ssl8FinalizeEv", ok}, {"nnsslInitialize", ok}, {"nnsslFinalize", ok},
        {"_ZN2nn3ssl7Context6CreateENS1_10SslVersionE", ssl_context_create},
        {"_ZN2nn3ssl7Context15ImportServerPkiEPmPKcjNS0_17CertificateFormatE", ssl_import_pki},
        {"nnsslContextDestroy", ssl_context_destroy}, {"nnsslContextGetContextId", ssl_context_id},
        {"nnsslConnectionCreate", ssl_conn_create}, {"nnsslConnectionDestroy", ssl_conn_destroy},
        {"nnsslConnectionSetSocketDescriptor", ssl_set_socket}, {"nnsslConnectionSetHostName", ssl_set_host},
        {"nnsslConnectionSetIoMode", ssl_set_io_mode}, {"nnsslConnectionSetVerifyOption", ssl_set_ignored},
        {"nnsslConnectionSetSessionCacheMode", ssl_set_ignored}, {"nnsslConnectionSetOption", ssl_set_ignored},
        {"nnsslConnectionDoHandshakeWithCertBuffer", ssl_handshake}, {"nnsslConnectionGetVerifyCertError", ssl_verify_error},
        {"nnsslConnectionGetConnectionId", ssl_conn_id},
        {"nnsslConnectionRead", ssl_read}, {"nnsslConnectionWrite", ssl_write}, {"nnsslConnectionPending", ssl_pending},
    };
    for (size_t i = 0; i < sizeof entries / sizeof *entries; i++) if (!strcmp(name, entries[i].name)) return entries[i].fn;
    return NULL;
}
