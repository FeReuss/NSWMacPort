/* nn::friends for SSBU 13.0.5 (SDK 8.2.1), from the ssbu-oof server's friend lists (standard v0.2 section 7, cached by
   oof_client.m). A friend's NetworkServiceAccountId is its PID, as the player's own (account.c); a friend connected to
   the server is "playing" this game, so friends' Battle Arenas (Matchmake Extension 109/51) find them.
   The SDK's own constructors and data accessors run unchanged (they touch no service); what would reach the friends
   service is answered here.
   Layouts read from SDK 8.2.1: Friend (0x200 bytes, sdk+0x1de580) keeps the owner's Uid at +0x00, the account ID at
   +0x10 (sdk+0x1de590), the nickname at +0x18 (sdk+0x1de5a0), its FriendPresence at +0x40 (sdk+0x1de690) and "valid" at
   +0x128 (sdk+0x1de6f0); FriendPresence keeps the last played application at +0x00 (sdk+0x1df300), the last time online
   at +0x10 (sdk+0x1df310), the status at +0x18 (sdk+0x1df2f0: 0 offline, 1 online, 2 playing) and "same presence group
   application" at +0x1c (sdk+0x1df3b0). Profile (0x100 bytes, sdk+0x1df480): account ID +0x00 (sdk+0x1df4d0), nickname
   +0x08 (sdk+0x1df4e0), "valid" +0xd0 (sdk+0x1df590).
   AsyncContext keeps a mutex at +0x00 and its service object at +0x20 (sdk+0x1de230); NotificationQueue keeps its
   service object at +0x30 and a SystemEvent at +0x38 that GetSystemEvent returns while +0x30 is set (sdk+0x1dedf0);
   UserPresence starts with the user's Uid (sdk+0x1deff0). */
#include "runtime.h"
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define FRIENDS_NOT_STARTED 0xe79u   /* the SDK's own GetResult answer for a context nothing ran in */
/* shortcut: one "nothing queued" code for Pop (the game stops popping on any non-zero result, main+0x22dc79c);
   take the console's exact code if a game ever tells them apart. */
#define FRIENDS_QUEUE_EMPTY (121u | (6u << 9))

static uint32_t ok(void) { return 0; }

enum { FRIEND_SIZE = 0x200, PROFILE_SIZE = 0x100, PLAYING = 2 };
static const uint64_t SSBU_APPLICATION = 0x01006A800016E000ull;
static void fill_friend(uint8_t *f, const void *uid, const RuntimeOofFriend *o) {
    memset(f, 0, FRIEND_SIZE);
    if (uid) memcpy(f, uid, 16);
    memcpy(f + 0x10, &o->pid, 8);
    memcpy(f + 0x18, o->name, sizeof o->name);
    if (o->online) {
        uint32_t status = PLAYING; uint64_t now = (uint64_t)time(NULL);
        memcpy(f + 0x40, &SSBU_APPLICATION, 8); memcpy(f + 0x48, &SSBU_APPLICATION, 8);   /* application, presence group */
        memcpy(f + 0x50, &now, 8); memcpy(f + 0x58, &status, 4); f[0x5c] = 1;
    }
    f[0x128] = 1;
}
/* FriendFilter: the first word is the presence status wanted (0 any); every other value keeps the friends playing. */
static uint32_t friend_list(int *count, void *out, const void *uid, int offset, int max, const void *filter) {
    if (!count || (max > 0 && !out) || offset < 0) return 0x7c79;
    uint32_t want = 0; if (filter) memcpy(&want, filter, 4);
    RuntimeOofFriend all[RUNTIME_OOF_FRIENDS_MAX]; int n = runtime_oof_friends(all, RUNTIME_OOF_FRIENDS_MAX), k = 0;
    for (int i = 0, seen = 0; i < n && k < max; i++) {
        if (want && !all[i].online) continue;
        if (seen++ < offset) continue;
        fill_friend((uint8_t *)out + (size_t)k++ * FRIEND_SIZE, uid, &all[i]);
    }
    static int last = -1; if (k != last) { last = k; fprintf(stderr, "[friends] GetFriendList (presence filter %u): %d of %d friends\n", want, k, n); }
    *count = k; return 0;
}
static uint32_t blocked_list(int *count, void *out, const void *uid, int offset, int max) {
    (void)uid;
    if (!count || (max > 0 && !out) || offset < 0) return 0x7c79;
    uint64_t all[RUNTIME_OOF_FRIENDS_MAX]; int n = runtime_oof_blocked(all, RUNTIME_OOF_FRIENDS_MAX), k = 0;
    for (int i = offset; i < n && k < max; i++) memcpy((uint8_t *)out + (size_t)k++ * 8, &all[i], 8);
    *count = k; return 0;
}
static const RuntimeOofFriend *find(const RuntimeOofFriend *all, int n, uint64_t pid) {
    for (int i = 0; i < n; i++) if (all[i].pid == pid) return &all[i];
    return NULL;
}
/* Friend::Update: each friend again from the cached list (one that is no longer a friend becomes invalid). */
static uint32_t update_friend_info(void *friends, const void *uid, const void *ids, int n) {
    if (n < 0 || (n && (!friends || !ids))) return 0x7c79;
    RuntimeOofFriend all[RUNTIME_OOF_FRIENDS_MAX]; int count = runtime_oof_friends(all, RUNTIME_OOF_FRIENDS_MAX);
    for (int i = 0; i < n; i++) {
        uint64_t pid; memcpy(&pid, (const uint8_t *)ids + (size_t)i * 8, 8);
        uint8_t *f = (uint8_t *)friends + (size_t)i * FRIEND_SIZE; const RuntimeOofFriend *o = find(all, count, pid);
        if (o) fill_friend(f, uid, o); else f[0x128] = 0;
    }
    return 0;
}
static uint32_t add_play_history(const void *uid, const void *key, const void *name, const void *own) { (void)uid; (void)key; (void)name; (void)own; return 0; }
static uint32_t play_history_key(uint8_t *key, const void *uid, unsigned char local) {   /* PlayHistoryRegistrationKey, 0x40 bytes */
    (void)uid; (void)local; if (!key) return 0x7c79; memset(key, 0, 0x40); return 0;
}
static uint32_t show_user_detail(const void *uid, const void *id, const void *a, const void *b) { (void)uid; (void)id; (void)a; (void)b; return 0; }
static uint32_t presence_init(uint8_t *presence, const uint8_t *uid) { if (!presence || !uid) return 0x7c79; memcpy(presence, uid, 16); return 0; }

/* AsyncContext: the one asynchronous call (GetProfileList) finishes before it returns; +0x20 marks it done. */
typedef struct { uint8_t event[0x100]; } AsyncDone;
static AsyncDone **async_slot(void *ctx) { return (AsyncDone **)((uint8_t *)ctx + 0x20); }
/* Profiles of friends from the cached list; anyone else stays as constructed (not valid, no nickname). */
static uint32_t profile_list(void *ctx, void *profiles, const void *uid, const void *ids, int n) {
    (void)uid;
    if (!ctx || n < 0 || (n && (!profiles || !ids))) return 0x7c79;
    RuntimeOofFriend all[RUNTIME_OOF_FRIENDS_MAX]; int count = runtime_oof_friends(all, RUNTIME_OOF_FRIENDS_MAX);
    for (int i = 0; i < n; i++) {
        uint64_t pid; memcpy(&pid, (const uint8_t *)ids + (size_t)i * 8, 8);
        const RuntimeOofFriend *o = find(all, count, pid); if (!o) continue;
        uint8_t *p = (uint8_t *)profiles + (size_t)i * PROFILE_SIZE;
        memset(p, 0, PROFILE_SIZE); memcpy(p, &pid, 8); memcpy(p + 8, o->name, sizeof o->name); p[0xd0] = 1;
    }
    if (!*async_slot(ctx) && !(*async_slot(ctx) = calloc(1, sizeof(AsyncDone)))) runtime_fail("friends async record");
    return 0;
}
static uint32_t async_result(const void *ctx) { return *async_slot((void *)ctx) ? 0 : FRIENDS_NOT_STARTED; }
static uint32_t async_event(void *ctx, void *event) {
    if (!event || !*async_slot(ctx)) return FRIENDS_NOT_STARTED;
    hle_bind_system_event(event, 0); hle_signal_system_event(event); return 0;
}
static void async_destroy(void *ctx) { free(*async_slot(ctx)); *async_slot(ctx) = NULL; }

/* NotificationQueue: oof_client.m's refreshes queue what changed and fire the queue's event; the game then pops
   NotificationInfo {u32 type, u64 account ID} (main+0x22dc46c: type 1 reads the whole list again, type 2 that friend). */
enum { QUEUED_MAX = 64 };
static pthread_mutex_t queueLock = PTHREAD_MUTEX_INITIALIZER;
static uint8_t *queueObject;                                    /* queueLock: the game's queue, while initialized */
static struct { uint32_t type; uint64_t pid; } queued[QUEUED_MAX];
static int queuedHead, queuedCount;                             /* queueLock */
void runtime_friends_notify(uint32_t type, uint64_t pid) {
    pthread_mutex_lock(&queueLock);
    if (queueObject) {
        if (queuedCount == QUEUED_MAX) { queuedHead = (queuedHead + 1) % QUEUED_MAX; queuedCount--; }   /* drop the oldest */
        int at = (queuedHead + queuedCount++) % QUEUED_MAX; queued[at].type = type; queued[at].pid = pid;
        hle_signal_system_event(queueObject + 0x38);
    }
    pthread_mutex_unlock(&queueLock);
    fprintf(stderr, "[friends] notification %u%s\n", type, type == 1 ? " (list changed)" : " (a friend's presence)");
}
static uint32_t queue_init(uint8_t *queue, const void *uid) {
    (void)uid; if (!queue) return 0x7c79;
    if (!*(uint64_t *)(void *)(queue + 0x30)) { hle_bind_system_event(queue + 0x38, 0); *(uint64_t *)(void *)(queue + 0x30) = 1; }
    pthread_mutex_lock(&queueLock); queueObject = queue; queuedCount = 0; pthread_mutex_unlock(&queueLock);
    return 0;
}
static uint32_t queue_pop(void *queue, void *info) {
    (void)queue;
    if (!info) return 0x7c79;
    pthread_mutex_lock(&queueLock);
    uint32_t r = FRIENDS_QUEUE_EMPTY;
    if (queuedCount) {
        uint8_t *out = info; memset(out, 0, 16);
        memcpy(out, &queued[queuedHead].type, 4); memcpy(out + 8, &queued[queuedHead].pid, 8);
        queuedHead = (queuedHead + 1) % QUEUED_MAX; queuedCount--; r = 0;
    }
    pthread_mutex_unlock(&queueLock);
    return r;
}
static void queue_finalize(uint8_t *queue) {
    if (!queue) return;
    *(uint64_t *)(void *)(queue + 0x30) = 0;
    pthread_mutex_lock(&queueLock); if (queueObject == queue) queueObject = NULL; pthread_mutex_unlock(&queueLock);
}

void *runtime_friends_override(const char *name) {
    static const struct { const char *name; void *fn; } entries[] = {
        {"_ZN2nn7friends10InitializeEv", ok},
        {"_ZN2nn7friends13GetFriendListEPiPNS0_6FriendERKNS_7account3UidEiiRKNS0_12FriendFilterE", friend_list},
        {"_ZN2nn7friends18GetBlockedUserListEPiPNS_7account23NetworkServiceAccountIdERKNS2_3UidEii", blocked_list},
        {"_ZN2nn7friends16UpdateFriendInfoEPNS0_6FriendERKNS_7account3UidEPKNS3_23NetworkServiceAccountIdEi", update_friend_info},
        {"_ZN2nn7friends14AddPlayHistoryERKNS_7account3UidERKNS0_26PlayHistoryRegistrationKeyERKNS0_15InAppScreenNameESA_", add_play_history},
        {"_ZN2nn7friends29GetPlayHistoryRegistrationKeyEPNS0_26PlayHistoryRegistrationKeyERKNS_7account3UidEb", play_history_key},
        {"_ZN2nn7friends18ShowUserDetailInfoERKNS_7account3UidERKNS1_23NetworkServiceAccountIdERKNS0_15InAppScreenNameESA_", show_user_detail},
        {"_ZN2nn7friends12UserPresence10InitializeERKNS_7account3UidE", presence_init},
        {"_ZN2nn7friends14GetProfileListEPNS0_12AsyncContextEPNS0_7ProfileERKNS_7account3UidEPKNS5_23NetworkServiceAccountIdEi", profile_list},
        {"_ZNK2nn7friends12AsyncContext9GetResultEv", async_result},
        {"_ZN2nn7friends12AsyncContext14GetSystemEventEPNS_2os11SystemEventE", async_event},
        {"_ZN2nn7friends12AsyncContextD1Ev", async_destroy},
        {"_ZN2nn7friends17NotificationQueue10InitializeERKNS_7account3UidE", queue_init},
        {"_ZN2nn7friends17NotificationQueue3PopEPNS0_16NotificationInfoE", queue_pop},
        {"_ZN2nn7friends17NotificationQueue8FinalizeEv", queue_finalize},
        {"_ZN2nn7friends17NotificationQueueD1Ev", queue_finalize},
    };
    for (size_t i = 0; i < sizeof entries / sizeof *entries; i++) if (!strcmp(name, entries[i].name)) return entries[i].fn;
    return NULL;
}
