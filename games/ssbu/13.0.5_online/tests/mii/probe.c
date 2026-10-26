/* nn::mii (mii_host.c): the six default Miis are three men and three women (Mob Smash needs three of each). No game. */
#include "../../runtime/mii_host.c"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

_Noreturn void runtime_fail(const char *why) { fprintf(stderr, "FAIL: %s\n", why); exit(1); }

int main(void) {
    void (*build)(const uint64_t *, uint8_t *, int) =
        (void (*)(const uint64_t *, uint8_t *, int))runtime_mii_override("_ZNK2nn3mii8Database12BuildDefaultEPNS0_8CharInfoEi");
    assert(build);
    uint64_t db = 1; uint8_t c[0x58]; int women = 0;
    for (int i = 0; i < 6; i++) { build(&db, c, i); assert(c[0x28] <= 1); women += c[0x28]; }
    assert(women == 3);
    puts("pass: three of each");
    return 0;
}
