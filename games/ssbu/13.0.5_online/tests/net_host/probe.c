/* The ssbu-oof standard's port services (net_host.c) against local sockets: no game, no server, no Nintendo host. */
#include "../../runtime/net_host.c"
#include <assert.h>

_Noreturn void runtime_fail(const char *why) { fprintf(stderr, "FAIL: %s\n", why); exit(1); }
int runtime_oof_enabled(void) { return 1; }
int runtime_ldn_active(void) { return 0; }
int runtime_core_away(void) { return 1; }
void runtime_core_back(int *away) { assert(*away == 1); *away = 0; }
int runtime_oof_nat_check(struct in_addr *out) { inet_aton("127.0.0.1", &out[0]); inet_aton("127.0.0.2", &out[1]); return 1; }
int runtime_module_offset(uint64_t pc, const char **name, uint64_t *off) { (void)pc; (void)name; (void)off; return 0; }

/* probe tls: the host TLS path against the test's TLS echo server (OOF_SERVER, OOF_TLS=1, maybe OOF_CA). Exit 3: refused. */
static int tls_probe(void) {
    assert(online() == 1);
    int fd = sock_socket(AF_INET, SOCK_STREAM, 0); assert(fd >= 0);
    struct sockaddr_in to = {.sin_len = sizeof to, .sin_family = AF_INET, .sin_port = htons(server_port), .sin_addr = server_ip};
    assert(sock_connect(fd, (struct sockaddr *)&to, sizeof to) == 0);
    char conn[64] = {0}, buf[64]; int n = 0;
    assert(ssl_conn_create(conn, NULL) == 0 && ssl_set_socket(conn, fd) == 0 && ssl_set_io_mode(conn, 2) == 0);
    if (ssl_handshake(conn, NULL, NULL) != 0) { puts("handshake refused"); ssl_conn_destroy(conn); return 3; }
    assert(ssl_write(conn, "ping", &n, 4) == 0 && n == 4);
    n = 0; struct pollfd p = {.fd = fd, .events = POLLIN};
    for (int i = 0; i < 50 && ssl_read(conn, buf, &n, sizeof buf) == SSL_WOULD_BLOCK; i++) poll(&p, 1, 100);
    assert(n == 4 && !memcmp(buf, "pong", 4));   /* the server's answer, decrypted */
    assert(ssl_conn_destroy(conn) == 0);
    puts("tls ok"); return 0;
}

int main(int argc, char **argv) {
    if (argc > 1 && !strcmp(argv[1], "tls")) return tls_probe();
    int server = socket(AF_INET, SOCK_STREAM, 0); assert(server >= 0);
    struct sockaddr_in sa = {.sin_len = sizeof sa, .sin_family = AF_INET, .sin_addr.s_addr = htonl(INADDR_LOOPBACK)};
    assert(!bind(server, (struct sockaddr *)&sa, sizeof sa) && !listen(server, 1));
    socklen_t sl = sizeof sa; getsockname(server, (struct sockaddr *)&sa, &sl);
    char env[64]; snprintf(env, sizeof env, "127.0.0.1:%u", ntohs(sa.sin_port));
    setenv("OOF_SERVER", env, 1); setenv("OOF_TLS", "0", 1); setenv("OOF_NET_TRACE", "1", 1);

    /* trace decoder: a masked client frame, PRUDP lite DATA (reliable, needs ACK), RMC request 21/41 with a u32 body */
    unsigned char plain[29] = {0x80, 0, 17, 0, 0xff, 0xf, 1, 0, 0x62, 0, 1, 0, 13, 0, 0, 0, 0x80 | 21, 7, 0, 0, 0, 41, 0, 0, 0, 1, 0, 0, 0};
    unsigned char frame[6 + sizeof plain] = {0x82, 0x80 | sizeof plain, 1, 2, 3, 4};
    for (size_t i = 0; i < sizeof plain; i++) frame[6 + i] = plain[i] ^ frame[2 + i % 4];
    trace_rmc("Write", frame, sizeof frame);

    char fqdn[256] = "g23380901-%.s.n.srv.nintendo.net", out[256];
    assert(resolve_ex(out, fqdn) == 0 && !strcmp(out, "g23380901-lp1.s.n.srv.nintendo.net"));
    struct addrinfo *ai = NULL, hints = {.ai_family = AF_INET, .ai_socktype = SOCK_STREAM};
    assert(sock_getaddrinfo(out, NULL, &hints, &ai) == 0 && ai);
    struct sockaddr_in to = *(struct sockaddr_in *)ai->ai_addr; sock_freeaddrinfo(ai);
    assert(to.sin_addr.s_addr == htonl(INADDR_LOOPBACK));
    assert(sock_getaddrinfo("www.nintendo.com", NULL, &hints, &ai) == EAI_NONAME);   /* negative control: no other host */
    /* Pia's NAT check passes the pattern unexpanded; another name with "%" stays rejected */
    assert(sock_getaddrinfo("nncs1-%.n.n.srv.nintendo.net", NULL, &hints, &ai) == 0 && ai);
    assert(((struct sockaddr_in *)ai->ai_addr)->sin_addr.s_addr == htonl(INADDR_LOOPBACK)); sock_freeaddrinfo(ai);
    struct hostent *nncs2 = sock_gethostbyname("nncs2-%.n.n.srv.nintendo.net", NULL, 0);   /* the announced second address */
    assert(nncs2 && ((struct in_addr *)nncs2->h_addr_list[0])->s_addr == inet_addr("127.0.0.2"));
    assert(sock_getaddrinfo("ctest-%.n.n.srv.nintendo.net", NULL, &hints, &ai) == EAI_NONAME);
    NnAddrInfo *nn = NULL;   /* the nn::socket::AddrInfo overload: address at +0x18, as the game reads it */
    assert(sock_getaddrinfo_nn(out, NULL, &hints, &nn) == 0 && nn && nn->family == AF_INET);
    assert(((struct sockaddr_in *)*(struct sockaddr **)((char *)nn + 0x18))->sin_addr.s_addr == htonl(INADDR_LOOPBACK));
    sock_freeaddrinfo_nn(nn);

    int fd = sock_socket(AF_INET, SOCK_STREAM, 0); assert(fd >= 0);
    to.sin_port = htons(443);   /* the game's wss:// port goes to the server's own port */
    assert(sock_connect(fd, (struct sockaddr *)&to, sizeof to) == 0);
    int peer = accept(server, NULL, NULL); assert(peer >= 0);

    char conn[0x40], buf[8]; int n = -1;
    assert(ssl_conn_create(conn, NULL) == 0 && ssl_set_socket(conn, fd) == 0 && ssl_set_io_mode(conn, 2) == 0);
    assert(ssl_handshake(conn, NULL, NULL) == 0);
    assert(ssl_read(conn, buf, &n, sizeof buf) == SSL_WOULD_BLOCK);
    assert(write(peer, "hi", 2) == 2);
    struct pollfd p = {.fd = fd, .events = POLLIN}; assert(sock_poll(&p, 1, 1000) == 1);
    assert(ssl_pending(conn, &n) == 0 && n == 2);
    assert(ssl_read(conn, buf, &n, sizeof buf) == 0 && n == 2 && !memcmp(buf, "hi", 2));
    assert(ssl_write(conn, "ok", &n, 2) == 0 && n == 2 && read(peer, buf, 2) == 2 && !memcmp(buf, "ok", 2));
    assert(ssl_conn_destroy(conn) == 0 && read(peer, buf, 1) == 0);   /* Destroy closed the socket */

    int u1 = sock_socket(AF_INET, SOCK_DGRAM, 0), u2 = sock_socket(AF_INET, SOCK_DGRAM, 0);
    struct sockaddr_in ua = {.sin_len = sizeof ua, .sin_family = AF_INET};
    assert(sock_bind(u1, (struct sockaddr *)&ua, sizeof ua) == 0);
    sl = sizeof ua; assert(sock_getsockname(u1, (struct sockaddr *)&ua, &sl) == 0);
    assert(sock_bind(u2, (struct sockaddr *)&ua, sizeof ua) == 0);   /* taken: another free port */
    struct sockaddr_in ub; sl = sizeof ub; assert(sock_getsockname(u2, (struct sockaddr *)&ub, &sl) == 0 && ub.sin_port != ua.sin_port);
    /* Pia polls its UDP socket without waiting: an empty queue must read as Horizon's EAGAIN (11), not macOS's 35 */
    assert(sock_recvfrom(u1, buf, sizeof buf, MSG_DONTWAIT, NULL, NULL) == -1 && sock_errno() == 11);
    assert(sock_connect(u2, (struct sockaddr *)&(struct sockaddr_in){.sin_len = sizeof ua, .sin_family = AF_INET}, sizeof ua) == -1 && sock_errno() == 99);   /* EADDRNOTAVAIL: macOS 49 */
    /* OOF_NAT_ALIAS: nncs2 appears at 192.0.2.2, datagrams to it reach the real address, its answers come back from it */
    setenv("OOF_NAT_ALIAS", "1", 1);
    nncs2 = sock_gethostbyname("nncs2-%.n.n.srv.nintendo.net", NULL, 0);
    assert(nncs2 && ((struct in_addr *)nncs2->h_addr_list[0])->s_addr == inet_addr("192.0.2.2"));
    struct sockaddr_in to1 = ua; to1.sin_addr.s_addr = inet_addr("192.0.2.1"); sl = sizeof ua;   /* nncs1 = 127.0.0.1 here */
    assert(sock_getsockname(u1, (struct sockaddr *)&ua, &sl) == 0);
    to1.sin_port = ua.sin_port;
    assert(sock_sendto(u2, "nat", 3, 0, (struct sockaddr *)&to1, sizeof to1) == 3 && recv(u1, buf, sizeof buf, 0) == 3);
    struct sockaddr_in answer = {.sin_len = sizeof answer, .sin_family = AF_INET, .sin_port = htons(10125), .sin_addr.s_addr = inet_addr("127.0.0.2")};
    nat_alias_from((struct sockaddr *)&answer); assert(answer.sin_addr.s_addr == inet_addr("192.0.2.2"));
    struct sockaddr_in other = answer; other.sin_port = htons(50000); other.sin_addr.s_addr = inet_addr("127.0.0.2");
    nat_alias_from((struct sockaddr *)&other); assert(other.sin_addr.s_addr == inet_addr("127.0.0.2"));   /* a peer on that IP stays */
    /* OOF_VIRTUAL_INDEX: instance 2 is 198.51.100.2 on ports 51000…; datagrams to another virtual instance reach this Mac and
       come back from its virtual address; a NAT check answer says the virtual address was seen */
    unsetenv("OOF_NAT_ALIAS"); setenv("OOF_VIRTUAL_INDEX", "2", 1);
    struct in_addr me; assert(lan_address(&me, NULL) && me.s_addr == inet_addr("198.51.100.2"));
    int v1 = sock_socket(AF_INET, SOCK_DGRAM, 0), v2 = sock_socket(AF_INET, SOCK_DGRAM, 0);
    struct sockaddr_in want = {.sin_len = sizeof want, .sin_family = AF_INET, .sin_port = htons(12345)};
    assert(sock_bind(v1, (struct sockaddr *)&want, sizeof want) == 0);
    struct sockaddr_in got; sl = sizeof got; assert(sock_getsockname(v1, (struct sockaddr *)&got, &sl) == 0 && ntohs(got.sin_port) == 51345);
    assert(sock_bind(v2, (struct sockaddr *)&want, sizeof want) == 0);   /* taken: the next port of the range */
    sl = sizeof got; assert(sock_getsockname(v2, (struct sockaddr *)&got, &sl) == 0 && ntohs(got.sin_port) == 51346);
    struct sockaddr_in peer2 = {.sin_len = sizeof peer2, .sin_family = AF_INET, .sin_port = htons(51345), .sin_addr.s_addr = inet_addr("198.51.100.2")};
    assert(sock_sendto(v2, "hey", 3, 0, (struct sockaddr *)&peer2, sizeof peer2) == 3);
    struct sockaddr_in from; socklen_t fl = sizeof from;
    assert(sock_recvfrom(v1, buf, sizeof buf, 0, (struct sockaddr *)&from, &fl) == 3 && from.sin_addr.s_addr == inet_addr("198.51.100.2") && ntohs(from.sin_port) == 51346);
    unsigned char seen[16] = {0, 0, 0, 0x65, 0, 0, 0xc8, 0x51, 127, 0, 0, 1, 127, 0, 0, 1};
    struct sockaddr_in nat = {.sin_len = sizeof nat, .sin_family = AF_INET, .sin_port = htons(10025), .sin_addr.s_addr = htonl(INADDR_LOOPBACK)};
    virt_from((struct sockaddr *)&nat, seen, 16); assert(!memcmp(seen + 8, (unsigned char[]){198, 51, 100, 2}, 4) && nat.sin_addr.s_addr == htonl(INADDR_LOOPBACK));
    unsetenv("OOF_VIRTUAL_INDEX");
    puts("ssbu-oof port services ok");
    return 0;
}
