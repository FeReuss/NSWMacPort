#include "../games/totk/runtime/runtime.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { uint64_t lo, hi; } Uid;
typedef struct { Uid uid; uint64_t token; } UserHandle;
void *runtime_account_override(const char *name);
_Noreturn void runtime_fail(const char *message) { fprintf(stderr, "%s\n", message); abort(); }

int main(void) {
    typedef uint32_t (*List)(int *, Uid *, int);
    typedef uint32_t (*Pop)(int *, UserHandle *, int);
    List list = runtime_account_override("_ZN2nn7account12ListAllUsersEPiPNS0_3UidEi");
    Pop pop = runtime_account_override("_ZN2nn7account12PopOpenUsersEPiPNS0_10UserHandleEi");
    assert(list && pop);
    Uid users[2] = {{0}, {123, 456}};
    int count = -1;
    assert(!list(&count, users, 2) && count == 1);
    assert(users[0].lo || users[0].hi);
    assert(users[1].lo == 123 && users[1].hi == 456);
    Uid second = {0};
    assert(!list(&count, &second, 1) && count == 1);
    assert(!memcmp(&second, &users[0], sizeof second));
    assert(!list(&count, NULL, 0) && count == 0);
    assert(list(NULL, users, 1) && list(&count, NULL, 1) && list(&count, users, -1));
    UserHandle pending[2], before[2];
    memset(pending, 0xa5, sizeof pending); memcpy(before, pending, sizeof pending);
    assert(!pop(&count, pending, 2) && count == 0);
    assert(!memcmp(pending, before, sizeof pending));
    assert(!pop(&count, NULL, 0) && count == 0);
    assert(pop(NULL, pending, 1) && pop(&count, NULL, 1) && pop(&count, pending, -1));
    typedef unsigned (*Open)(UserHandle *);
    typedef uint32_t (*Id)(Uid *, const UserHandle *);
    typedef void (*Close)(const UserHandle *);
    Open open_user = runtime_account_override("_ZN2nn7account22TryOpenPreselectedUserEPNS0_10UserHandleE");
    Id user_id = runtime_account_override("_ZN2nn7account9GetUserIdEPNS0_3UidERKNS0_10UserHandleE");
    Close close_user = runtime_account_override("_ZN2nn7account9CloseUserERKNS0_10UserHandleE");
    UserHandle handle; Uid opened;
    assert(open_user(&handle) && !user_id(&opened, &handle));
    assert(!memcmp(&opened, &users[0], sizeof opened));
    close_user(&handle);
    assert(user_id(&opened, &handle));
    puts("local account listing, bounds and handles passed");
    return 0;
}
