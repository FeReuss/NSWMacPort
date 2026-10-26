/* CPU only: the production scheduler must preempt guest polls, preserve core masks,
   resume displaced owners, and leave host code and higher-priority owners alone. */
#include "../../runtime/runtime.h"
#include <mach/mach_time.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

static atomic_int entered, stop, resumed, urgent_ran;
static atomic_ulong spins;
static int guest = 1, expected_core, migrate, rotate;
static void urgent(uint64_t arg);
int runtime_current_core(void);
_Noreturn void runtime_fail(const char *message) { fprintf(stderr, "FAIL: %s\n", message); exit(1); }
void *runtime_symbol(const char *name) { (void)name; runtime_fail("unexpected guest symbol"); }
void runtime_thread_init(void) {}
uint64_t runtime_now_ns(void) { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }
static void need(int ok, const char *why) { if (!ok) runtime_fail(why); }
static void kernel(int svc, uint64_t *r) { need(svc_thread(svc, r), "SVC dispatch"); }
static void finish(void) { uint64_t r[31] = {0}; kernel(0x0a, r); runtime_fail("ExitThread returned"); }
__attribute__((noinline)) static void spinner(uint64_t arg) {
    (void)arg;
    need(runtime_current_core() == expected_core, "spinner initial affinity");
    atomic_store(&entered, 1);
    while (!atomic_load_explicit(&stop, memory_order_acquire)) atomic_fetch_add(&spins, 1);
    need(runtime_current_core() == (migrate ? 2 : expected_core), "spinner resumed affinity");
    atomic_store(&resumed, 1); finish();
}
int runtime_guest_text(uint64_t pc) {
#ifdef NEGATIVE
    (void)pc; return 0;
#else
    return guest && ((pc >= (uintptr_t)spinner && pc < (uintptr_t)spinner + 256) ||
                     (pc >= (uintptr_t)urgent && pc < (uintptr_t)urgent + 512));
#endif
}
__attribute__((noinline)) static void urgent(uint64_t arg) {
    (void)arg; need(runtime_current_core() == expected_core, "waiter affinity");
    atomic_store(&urgent_ran, 1);
    for (int i = 0; rotate && i < 4; i++) {
        unsigned long before = atomic_load(&spins);
        uint64_t deadline = runtime_now_ns() + 250000000;
        while (atomic_load(&spins) == before && runtime_now_ns() < deadline) {}
        need(atomic_load(&spins) != before, "parked equal-priority owner received another timeslice");
    }
    atomic_store(&stop, 1);
    if (migrate) {
        uint64_t deadline = runtime_now_ns() + 500000000;
        while (!atomic_load(&resumed) && runtime_now_ns() < deadline) {}
        need(atomic_load(&resumed), "displaced owner migrated to idle core");
    }
    finish();
}
static uint32_t start(void (*entry)(uint64_t), int priority) {
    void *stack = mmap(NULL, 1 << 20, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    need(stack != MAP_FAILED, "stack allocation");
    uint64_t r[31] = {0}; r[1] = (uintptr_t)entry; r[3] = (uintptr_t)stack + (1 << 20);
    r[4] = (uint64_t)priority; r[5] = (uint64_t)expected_core; kernel(8, r); need(!r[0], "CreateThread");
    uint32_t h = (uint32_t)r[1];
    if (migrate && entry == spinner) {
        r[0] = h; r[1] = 1; r[2] = 6; kernel(0x0f, r); need(!r[0], "SetThreadCoreMask");
    }
    r[0] = h; kernel(9, r); need(!r[0], "StartThread"); return h;
}
static void wait_thread(uint32_t h) {
    uint64_t r[31] = {0}; r[1] = (uintptr_t)&h; r[2] = 1; r[3] = 1000000000;
    kernel(0x18, r); need(!r[0], "higher-priority waiter made progress");
    r[0] = h; kernel(0x16, r); need(!r[0], "CloseHandle");
}
static void reset(void) { entered = stop = resumed = urgent_ran = 0; spins = 0; }
static void wait_entered(void) {
    uint64_t deadline = runtime_now_ns() + 1000000000;
    while (!atomic_load(&entered) && runtime_now_ns() < deadline) usleep(100);
    need(atomic_load(&entered), "spinner started");
}
static void *release_host(void *unused) {
    (void)unused; usleep(30000);
    need(!atomic_load(&urgent_ran), "host code or higher-priority owner was preempted");
    atomic_store(&stop, 1); return NULL;
}
int main(void) {
    svc_thread_main_init();
    for (int i = 0; i < 32; i++) {
        reset(); expected_core = 1 + i % 2;
        uint32_t owner = start(spinner, 44); wait_entered();
        uint32_t waiter = start(urgent, i % 2 ? 44 : 30); wait_thread(waiter); wait_thread(owner);
        need(atomic_load(&resumed) && atomic_load(&urgent_ran), "displaced owner resumed");
    }
    reset(); expected_core = 1; rotate = 1;
    uint32_t rotating_owner = start(spinner, 44); wait_entered();
    uint32_t rotating_waiter = start(urgent, 44); wait_thread(rotating_waiter); wait_thread(rotating_owner); rotate = 0;
    reset(); expected_core = 1; migrate = 1;
    uint32_t owner = start(spinner, 44); wait_entered();
    uint32_t waiter = start(urgent, 30); wait_thread(waiter); wait_thread(owner); migrate = 0;
    for (int i = 0; i < 2; i++) {
        reset(); guest = i; expected_core = 1;
        uint32_t owner = start(spinner, i ? 30 : 44); wait_entered();
        uint32_t waiter = start(urgent, i ? 44 : 30);
        pthread_t host; need(!pthread_create(&host, NULL, release_host, NULL), "host thread");
        wait_thread(waiter); wait_thread(owner); pthread_join(host, NULL);
        need(atomic_load(&resumed), "protected owner finished");
    }
    puts("pass: 32 guest preemptions/resumptions; persistent rotation, host boundary, priority and affinity preserved");
    return 0;
}
