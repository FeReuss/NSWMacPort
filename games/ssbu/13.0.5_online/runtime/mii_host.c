/* nn::mii for SSBU 13.0.5 (SDK 8.2.1): the host's Mii database is empty, like a console where nobody saved a Mii. The
   SDK's CharInfo accessors run unchanged; what would reach the Mii service (mii:e) is answered here. Database keeps only
   its service object at +0x00 (sdk+0x2fc230), so a nonzero word there marks it initialized.
   Characters the game asks the service to build get one plain look, chosen here (mid-range values of each CharInfo field,
   0x58 bytes, field order as documented publicly for the Mii CharInfo format); none come from Nintendo's tables. */
#include "runtime.h"
#include <stdint.h>
#include <string.h>

/* shortcut: AppendMii opens the system's Mii editor, which the host does not have; it answers "cancelled" with the mii
   module's generic code until the console's exact one is known. */
#define MII_CANCELLED (126u | (1u << 9))

static uint32_t db_init(uint64_t *db) { if (db) *db = 1; return 0; }
static void db_destroy(uint64_t *db) { if (db) *db = 0; }
static int db_count(const uint64_t *db, int flag) { (void)db; (void)flag; return 0; }
static uint32_t db_get(uint64_t *db, int *count, void *out, int max, int flag) { (void)db; (void)out; (void)max; (void)flag; if (count) *count = 0; return 0; }
static unsigned char db_updated(uint64_t *db, int flag) { (void)db; (void)flag; return 0; }

static void plain_mii(uint8_t *c, unsigned seed) {
    memset(c, 0, 0x58);
    for (unsigned i = 0; i < 16; i++) c[i] = (uint8_t)(seed * 131u + i * 29u + 7u);   /* create ID, unique per seed */
    c[6] = (uint8_t)((c[6] & 0x0f) | 0x40); c[8] = (uint8_t)((c[8] & 0x3f) | 0x80);    /* shaped like a version-4 UUID */
    static const char name[] = "Mii";
    for (unsigned i = 0; name[i]; i++) c[0x10 + 2 * i] = (uint8_t)name[i];             /* UTF-16LE nickname */
    static const uint8_t fields[][2] = {   /* {offset, value}: mid-range proportions and positions */
        {0x29, 64}, {0x2a, 64}, {0x36, 4}, {0x37, 3}, {0x38, 4}, {0x39, 2}, {0x3a, 12}, {0x3d, 4}, {0x3e, 3}, {0x3f, 6},
        {0x40, 2}, {0x41, 10}, {0x43, 4}, {0x44, 9}, {0x47, 4}, {0x48, 3}, {0x49, 13}, {0x4d, 4}, {0x4e, 10}, {0x51, 4},
        {0x52, 10}, {0x54, 4}, {0x55, 2}, {0x56, 20}};
    for (unsigned i = 0; i < sizeof fields / sizeof *fields; i++) c[fields[i][0]] = fields[i][1];
}
/* Mob Smash sorts the six defaults by gender (CharInfo +0x28) and takes three of each (main+0x14eb638, std::vector::at),
   so a database without Miis must still give three men and three women, as the console's does. */
static void build_default(const uint64_t *db, uint8_t *out, int index) { (void)db; if (!out) runtime_fail("mii output"); plain_mii(out, (unsigned)index); out[0x28] = index >= 3; }
/* shortcut: random and converted characters get the same plain look; vary them when Mii Fighters need distinct faces. */
static void build_random(uint64_t *db, uint8_t *out, const void *desc) { (void)db; (void)desc; if (!out) runtime_fail("mii output"); plain_mii(out, 100); }
static void convert(uint64_t *db, uint8_t *out, const void *ver3) { (void)db; (void)ver3; if (!out) runtime_fail("mii output"); plain_mii(out, 200); }
static uint32_t append_mii(int *index) { (void)index; return MII_CANCELLED; }

void *runtime_mii_override(const char *name) {
    static const struct { const char *name; void *fn; } entries[] = {
        {"_ZN2nn3mii8Database10InitializeEv", db_init}, {"_ZN2nn3mii8DatabaseD1Ev", db_destroy},
        {"_ZNK2nn3mii8Database8GetCountEi", db_count}, {"_ZN2nn3mii8Database3GetEPiPNS0_8CharInfoEii", db_get},
        {"_ZN2nn3mii8Database9IsUpdatedEi", db_updated},
        {"_ZNK2nn3mii8Database12BuildDefaultEPNS0_8CharInfoEi", build_default},
        {"_ZN2nn3mii8Database11BuildRandomEPNS0_8CharInfoERKNS0_10RandomDescE", build_random},
        {"_ZN2nn3mii8Database7ConvertEPNS0_8CharInfoERKNS0_13Ver3StoreDataE", convert},
        {"_ZN2nn3mii9AppendMiiEPi", append_mii},
    };
    for (size_t i = 0; i < sizeof entries / sizeof *entries; i++) if (!strcmp(name, entries[i].name)) return entries[i].fn;
    return NULL;
}
