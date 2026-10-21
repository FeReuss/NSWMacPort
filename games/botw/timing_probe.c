/* Execute the player's rewritten VFR code against an independent time oracle.
   No game entry, graphics, audio device, profile or controller is opened. */
#include "runtime.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <libkern/OSCacheControl.h>

void runtime_game_patches(Module *m);
_Noreturn void runtime_fail(const char *message) { fprintf(stderr, "%s\n", message); exit(21); }
static void put32(uint8_t *p, size_t off, uint32_t v) { memcpy(p + off, &v, 4); }
static void putf(uint8_t *p, size_t off, float v) { memcpy(p + off, &v, 4); }
static float getf(const uint8_t *p, size_t off) { float v; memcpy(&v, p + off, 4); return v; }
static void ptr(uint8_t *p, size_t off, void *v) { memcpy(p + off, &v, 8); }
static void close_to(float have, double want) { assert(isfinite(have) && fabs(have - want) < 0.00001); }

int main(int argc, char **argv) {
    assert(argc == 2 || argc == 3);
    FILE *f = fopen(argv[1], "rb"); assert(f);
    struct stat st; assert(!fstat(fileno(f), &st));
    size_t data = ((size_t)st.st_size + 0x3fff) & ~0x3fffUL;
    Module m = {.name="main", .digest="6d48bff1b7f2692bd4f2efd3316b564877c50a10c6145610cf9a779b220538c6",
        .file_size=(size_t)st.st_size, .size=data + 0x4000, .text_end=0x15a1c64};
    m.base = mmap(NULL, m.size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0); assert(m.base != MAP_FAILED);
    assert(fread(m.base, 1, m.file_size, f) == m.file_size); fclose(f);
    if (argc == 3 && !strcmp(argv[2], "bad-word")) m.base[0x151d478] ^= 1;
    if (argc == 3 && !strcmp(argv[2], "bad-hash")) m.digest = "different rewrite";
    int baseline = argc == 3 && !strcmp(argv[2], "baseline");
    if (!baseline) runtime_game_patches(&m);
    assert(!mprotect(m.base, data, PROT_READ | PROT_EXEC)); sys_icache_invalidate(m.base, data);
    void (*update)(void *, unsigned) = (void (*)(void *, unsigned))(void *)(m.base + 0x151d3d0);
    unsigned checked = 0;
    for (unsigned mode = 0; mode < (baseline ? 1u : 5u); mode++) for (unsigned slow = 0; slow < 2; slow++) {
        _Alignas(8) uint8_t vfr[0x160] = {0};
        float multiplier[2][4] = {{0, 1, 1, 0}, {0, slow ? 0.5f : 1.0f, slow ? 0.5f : 1.0f, 0}};
        for (unsigned core = 0; core < 3; core++) {
            ptr(vfr, 0x48 + 8 * core, vfr + 0x34); ptr(vfr, 0x60 + 8 * core, vfr + 0x3c);
            for (unsigned lane = 0; lane < 4; lane++) ptr(vfr, 0xf0 + lane * 24 + core * 8, vfr + 0x8c + lane * 12 + core * 4);
        }
        put32(vfr, 0x78, 2); ptr(vfr, 0x80, multiplier); put32(vfr, 0x88, 3);
        put32(vfr, 0x150, 2); put32(vfr, 0x154, 30); putf(vfr, 0x158, 1.0f / 30.0f);
        put32(vfr, 0x34, 2); putf(vfr, 0x30, 1); putf(vfr, 0x3c, 1);
        uint64_t now = 1000000000ULL; runtime_frame_step_update(now);
        double elapsed = 0, simulated = 0; float previous = 0; unsigned current_interval = 2;
        for (unsigned n = 0; n < 600; n++) {
            unsigned hz = baseline ? 60 : mode < 3 ? (unsigned[]){30, 60, 120}[mode] : mode == 3 ? 18 + (n * 37) % 101 : 15;
            uint64_t ns = (1000000000ULL + hz / 2) / hz; now += ns;
            runtime_frame_step_update(now);
            unsigned interval = baseline ? 2 : n % 4; /* Changed, unchanged, and zero-interval paths. */
            update(vfr, interval);
            double dt = baseline ? 1.0 / 30.0 : fmin(1.0 / 15.0, fmax(1.0 / 120.0, ns * 1e-9));
            assert(vfr[0x28] == (interval && interval != current_interval)); checked++;
            if (interval) current_interval = interval;
            for (unsigned core = 0; core < 3; core++) {
                close_to(getf(vfr, 0x98 + core * 4), dt * 30 * (slow ? 0.5 : 1));
                close_to(getf(vfr, 0xb0 + core * 4), dt * (slow ? 0.5 : 1));
                close_to(getf(vfr, 0x8c + core * 4), slow ? 0.5 : 1);
                if (n) close_to(getf(vfr, 0xc8 + core * 4), previous);
                checked += 4;
            }
            previous = getf(vfr, 0x98); simulated += getf(vfr, 0xb0); elapsed += baseline ? ns * 1e-9 : dt;
        }
        assert(fabs(simulated - elapsed * (slow ? 0.5 : 1) * (baseline ? 2 : 1)) < 0.0001);
        checked++;
    }
    if (baseline) puts("{\"status\":\"pass\",\"scope\":\"unmodified VFR at 60 Hz with interval 2 advances simulation twice as fast\"}");
    else puts("{\"status\":\"pass\",\"scope\":\"original rewritten VFR at 15,30,60,120 and variable 18..118 Hz; slow motion, interval flags and per-core double buffers\"}");
    fprintf(stderr, "BOTW timing checks: %u\n", checked);
    assert(!munmap(m.base, m.size));
    return 0;
}
