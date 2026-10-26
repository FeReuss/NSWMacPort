/* friends_host.c: the server's friend list becomes SDK 8.2.1 Friend and Profile records (layouts in the file's header). */
#include "../../runtime/friends_host.c"
#include <assert.h>

_Noreturn void runtime_fail(const char *why) { fprintf(stderr, "FAIL: %s\n", why); exit(1); }
void hle_bind_system_event(void *event, int autoclear) { (void)event; (void)autoclear; }
static int signals;
void hle_signal_system_event(void *event) { (void)event; signals++; }
static const RuntimeOofFriend list[] = {{.pid = 1001, .name = "ana", .online = 0}, {.pid = 1002, .name = "bea", .online = 1}};
int runtime_oof_friends(RuntimeOofFriend *out, int max) { int n = max < 2 ? max : 2; memcpy(out, list, sizeof *out * (size_t)n); return n; }
int runtime_oof_blocked(uint64_t *out, int max) { if (max > 0) out[0] = 77; return max > 0; }

int main(void) {
    static uint8_t friends[3][FRIEND_SIZE]; const uint8_t uid[16] = {9, 9};
    int n = -1; uint32_t any = 0, playing = 3;
    assert(friend_list(&n, friends, uid, 0, 3, &any) == 0 && n == 2);
    uint64_t id; uint32_t status;
    memcpy(&id, friends[1] + 0x10, 8); memcpy(&status, friends[1] + 0x58, 4);
    assert(id == 1002 && !strcmp((char *)friends[1] + 0x18, "bea") && status == PLAYING && friends[1][0x5c] == 1 && friends[1][0x128] == 1);
    assert(friends[0][0x58] == 0 && friends[0][0x128] == 1 && friends[0][0] == 9);   /* ana: offline, valid, owner's Uid */
    assert(friend_list(&n, friends, uid, 0, 3, &playing) == 0 && n == 1);           /* a presence filter keeps bea only */
    memcpy(&id, friends[0] + 0x10, 8); assert(id == 1002);
    assert(friend_list(&n, friends, uid, 1, 3, &any) == 0 && n == 1);               /* offset */
    uint64_t ids[2] = {1001, 5}; static uint8_t profiles[2][PROFILE_SIZE]; uint8_t ctx[0x40] = {0};
    assert(profile_list(ctx, profiles, uid, ids, 2) == 0 && profiles[0][0xd0] == 1 && !strcmp((char *)profiles[0] + 8, "ana"));
    assert(profiles[1][0xd0] == 0);                                                    /* not a friend: not valid */
    async_destroy(ctx);
    friends[0][0x128] = 1; uint64_t gone = 5;
    assert(update_friend_info(friends, uid, &gone, 1) == 0 && friends[0][0x128] == 0);   /* no longer a friend */
    uint64_t blocked[4]; assert(blocked_list(&n, blocked, uid, 0, 4) == 0 && n == 1 && blocked[0] == 77);
    /* notifications: queued only while the game's queue exists, popped in order, then "empty" */
    static uint8_t queue[0x60]; uint8_t info[16];
    runtime_friends_notify(1, 0); assert(queue_pop(queue, info) == FRIENDS_QUEUE_EMPTY);   /* no queue yet: dropped */
    assert(queue_init(queue, uid) == 0);
    runtime_friends_notify(2, 1002); runtime_friends_notify(1, 0);
    uint32_t type; uint64_t who;
    assert(queue_pop(queue, info) == 0); memcpy(&type, info, 4); memcpy(&who, info + 8, 8); assert(type == 2 && who == 1002);
    assert(queue_pop(queue, info) == 0); memcpy(&type, info, 4); assert(type == 1);
    assert(queue_pop(queue, info) == FRIENDS_QUEUE_EMPTY);
    queue_finalize(queue); runtime_friends_notify(1, 0); assert(queue_pop(queue, info) == FRIENDS_QUEUE_EMPTY);
    assert(signals == 2);
    puts("friends ok");
    return 0;
}
