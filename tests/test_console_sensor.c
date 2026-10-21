#include "../games/totk/runtime/runtime.h"
#include <assert.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void *runtime_hid_feedback_override(const char *name);
static jmp_buf failure;
static int expect_failure;
_Noreturn void runtime_fail(const char *message) {
    if (expect_failure) longjmp(failure, 1);
    fprintf(stderr, "unexpected failure: %s\n", message);
    abort();
}
void runtime_rumble_set(unsigned side, float a, float f, float b, float g) {
    (void)side; (void)a; (void)f; (void)b; (void)g;
}
int runtime_motion_read(RuntimeMotionState *out, int max) { (void)out; (void)max; return 0; }
void runtime_motion_start(void) {}

#define REJECT(call) do { expect_failure = 1; if (!setjmp(failure)) { call; abort(); } expect_failure = 0; } while (0)
int main(void) {
    typedef void (*Init)(void *, size_t);
    typedef void (*Action)(void);
    typedef int (*States)(void *, int);
    Init init = runtime_hid_feedback_override("_ZN2nn3hid28InitializeSevenSixAxisSensorEPvm");
    Action finish = runtime_hid_feedback_override("_ZN2nn3hid26FinalizeSevenSixAxisSensorEv");
    Action start = runtime_hid_feedback_override("_ZN2nn3hid23StartSevenSixAxisSensorEv");
    Action stop = runtime_hid_feedback_override("_ZN2nn3hid22StopSevenSixAxisSensorEv");
    Action reset = runtime_hid_feedback_override("_ZN2nn3hid32ResetSevenSixAxisSensorTimestampEv");
    States states = runtime_hid_feedback_override("_ZN2nn3hid27GetSevenSixAxisSensorStatesEPNS0_23SevenSixAxisSensorStateEi");
    assert(init && finish && start && stop && reset && states);
    assert(!runtime_hid_feedback_override("unknown sensor API"));
    void (*drift)(const uint32_t *, const uint32_t *) = runtime_hid_feedback_override(
        "_ZN2nn3hid25SetGyroscopeZeroDriftModeERKNS0_19SixAxisSensorHandleERKNS0_22GyroscopeZeroDriftModeE");
    assert(drift);
    uint32_t handle = 0x22004, mode;
    for (mode = 0; mode < 3; mode++) drift(&handle, &mode);
    REJECT(drift(NULL, &mode));
    REJECT(drift(&handle, NULL));
    REJECT(drift(&handle, &mode));
    void *work = aligned_alloc(0x1000, 0x80000);
    assert(work);
    REJECT(start());
    REJECT(states(NULL, 0));
    REJECT(init(NULL, 0x80000));
    REJECT(init((char *)work + 1, 0x80000));
    REJECT(init(work, 0x7ffff));
    init(work, 0x80000);
    REJECT(init(work, 0x80000));
    start(); reset();
    unsigned char out[64], before[64];
    memset(out, 0xa5, sizeof out); memcpy(before, out, sizeof out);
    assert(states(out, 1) == 0);
    assert(!memcmp(out, before, sizeof out));
    assert(states(NULL, 0) == 0);
    REJECT(states(NULL, 1));
    REJECT(states(out, -1));
    stop(); finish();
    REJECT(reset());
    REJECT(finish());
    init(work, 0x80000); finish();
    free(work);
    puts("console sensor: lifecycle, unavailable samples and bounds passed");
    return 0;
}
