/* BOTW 1.9.0: keep VFR's slow-motion multipliers and buffered per-core deltas,
   but supply elapsed host time instead of the nominal display interval. */
#include "runtime.h"
#include <math.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static _Atomic uint32_t *ratio_data;
static _Atomic uint32_t pinned_us;
static uint64_t last_present_ns;

static void store_seconds(float seconds) {
    float ratio = seconds * 30.0f;
    uint32_t bits; memcpy(&bits, &ratio, sizeof bits);
    atomic_store_explicit(ratio_data, bits, memory_order_release);
}

void runtime_frame_step_pin(double ms) {
    if (!isfinite(ms) || ms < 0 || ms > 100) runtime_fail("BOTW frame step must be 0..100 ms");
    uint32_t us = (uint32_t)(ms * 1000.0 + 0.5);
    atomic_store(&pinned_us, us);
    if (ratio_data && us) store_seconds(us / 1e6f);
    fprintf(stderr, "[botw-time] %s (diagnostic pin %.3f ms)\n", us ? "pinned" : "real time", us / 1000.0);
}

void runtime_frame_step_update(uint64_t now_ns) {
    if (!ratio_data || !now_ns) return;
    uint64_t previous = last_present_ns; last_present_ns = now_ns;
    if (atomic_load_explicit(&pinned_us, memory_order_relaxed)) return;
    if (!previous || now_ns <= previous || now_ns - previous > 250000000ULL) {
        store_seconds(1.0f / 30.0f); return; /* Loading, suspension and debugger pauses do not become physics jumps. */
    }
    double dt = (now_ns - previous) * 1e-9;
    /* shortcut: VFR is verified at 15..120 Hz; lower rates need physics substeps before raising this bound. */
    if (dt < 1.0 / 120.0) dt = 1.0 / 120.0;
    if (dt > 1.0 / 15.0) dt = 1.0 / 15.0;
    store_seconds((float)dt);
}

static uint32_t adrp_x9(uintptr_t pc, uintptr_t target) {
    int64_t pages = (int64_t)(target >> 12) - (int64_t)(pc >> 12);
    if (pages < -(1 << 20) || pages >= (1 << 20)) runtime_fail("BOTW time data outside ADRP range");
    uint32_t imm = (uint32_t)pages & 0x1fffff;
    return 0x90000009u | ((imm & 3u) << 29) | ((imm >> 2) << 5);
}

void runtime_game_patches(Module *m) {
    if (strcmp(m->name, "main") || strcmp(m->digest, "6d48bff1b7f2692bd4f2efd3316b564877c50a10c6145610cf9a779b220538c6"))
        runtime_fail("BOTW timing requires the verified 1.9.0 main rewrite");
    const char *enabled = getenv("TOTK_REALTIME");
    if (enabled && !strcmp(enabled, "0")) return;
    uint64_t data = (m->file_size + 0x3fff) & ~0x3fffULL;
    if (data + 0x4000 != m->size || m->text_end < 0x151d494) runtime_fail("BOTW time-page layout");
    const uint32_t original[] = {0x14000003, 0xbd403261, 0x2a1f03e8, 0x1e202020, 0x3900a268, 0x54000040, 0xbd003e61};
    if (memcmp(m->base + 0x151d478, original, sizeof original)) runtime_fail("BOTW VFR original instructions differ");
    /* updateInterval copies A to B first. Both changed/unchanged interval paths
       then join at +0x480; preserve w8's interval-change flag and the original
       multiplier update + setMinDelta calls. x9 is dead until the next loop. */
    uint32_t words[] = {0x14000002, 0x2a1f03e8,
        adrp_x9((uintptr_t)m->base + 0x151d480, (uintptr_t)m->base + data),
        0xbd400121, 0x3900a268, 0xbd003261, 0xbd003e61};
    ratio_data = (_Atomic uint32_t *)(void *)(m->base + data);
    last_present_ns = 0; atomic_store(&pinned_us, 0); store_seconds(1.0f / 30.0f);
    memcpy(m->base + 0x151d478, words, sizeof words);
    fprintf(stderr, "[botw-time] VFR updateInterval: 7 guarded instructions; elapsed time, 15..120 Hz, original slow motion retained\n");
}

/* threads.c core-stall diagnostics: BOTW 1.9.0's job workers poll their sead job queue at main+0xef6354..0xef6418
   (x21 = the queue, w0 = this worker's participant index = TLS value - 1, x20 = the sead::Worker). Name the queue
   and its counters (+0x90 jobs, +0x94 taken, +0x98 "participant 0 starts" flag) so a stall shows who it waits for. */
int runtime_game_spin_note(uint64_t pc, const uint64_t *x, char *out, size_t n) {
    uint8_t *main_base = runtime_module_base("main");
    if (!main_base || pc < (uintptr_t)main_base + 0xef6354 || pc >= (uintptr_t)main_base + 0xef6418 || !x[21]) return 0;
    const uint8_t *q = (const uint8_t *)(uintptr_t)x[21]; uint32_t jobs, taken; memcpy(&jobs, q + 0x90, 4); memcpy(&taken, q + 0x94, 4);
    return snprintf(out, n, " jobq %#llx taken %u/%u flag %u participant %d", (unsigned long long)x[21], taken, jobs, q[0x98],
                    (int)(uint32_t)x[0]) > 0;   // w0 = TLS - 1 (main+0xef631c): only participant 0 may take the first batch
}
/* BOTW_CORE_TIMESLICE_MS may lend a polling worker's core only while it waits in the job-queue loop without its spinlock:
   [x21+0x1c] is taken by the stxr at 0xef6378 (or 0xef640c) and released by the stlxr at 0xef63e0. Stopped inside, it would
   leave participant 0 spinning on that lock until the lent core came back. Participant 0 itself (w0 = 0, the main thread) is
   never lent: it does not wait for anyone, and the frame waits for it. */
int runtime_game_spin_lendable(uint64_t pc, const uint64_t *x) {
    uint8_t *main_base = runtime_module_base("main"); if (!main_base) return 0;
    uint64_t o = pc - (uintptr_t)main_base;
    return ((o >= 0xef6354 && o < 0xef637c) || (o >= 0xef63e8 && o < 0xef6410)) && (uint32_t)x[0] != 0;
}
/* The waiting thread's own participant number, read on that thread: main+0x1573a9c stores GetCurrentCoreNumber() + 1 in
   the TLS slot *(u32 *)*(main+0x1d66320), and nn::os::GetTlsValue reads *(*(TLR+0x1f8) + 0x80 + slot*8) (sdk+0x143c60).
   Printed as the loop's index (TLS - 1), like the spinners' "participant". Logs before 2026-10-21 10:30 printed TLS (+1). */
int runtime_game_waiter_note(char *out, size_t n) {
    uint8_t *main_base = runtime_module_base("main"), *tlr = pthread_getspecific(runtime_keys[2]);
    uint64_t slot_at = 0, thread = 0, value; uint32_t slot;
    if (main_base) memcpy(&slot_at, main_base + 0x1d66320, 8);
    if (tlr) memcpy(&thread, tlr + 0x1f8, 8);
    if (!slot_at || !thread) return 0;
    memcpy(&slot, (const void *)(uintptr_t)slot_at, 4);
    if (slot >= 32) return 0;   // nn::os has 32 TLS slots; before the queue allocates its slot this is not one
    memcpy(&value, (const uint8_t *)(uintptr_t)thread + 0x80 + slot * 8, 8);
    return value && snprintf(out, n, " participant %llu", (unsigned long long)value - 1) > 0;
}

/* BOTW has no verified TOTK resolution, aspect or frame-controller patches. */
int runtime_output_res(unsigned *w, unsigned *h) { (void)w; (void)h; return 0; }
uint64_t runtime_output_res_extra_bytes(void) { return 0; }
uint64_t runtime_output_size_current(void) { return 0; }
int runtime_output_res_toggle(void) { return 0; }
int runtime_output_mode_cycle(void) { return 0; }
float runtime_aspect_now(void) { return 0; }
int runtime_aspect_toggle(void) { return 0; }
