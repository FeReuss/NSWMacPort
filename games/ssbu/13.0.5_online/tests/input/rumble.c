/* Local multiplayer rumble (hid_feedback.c): each vibration handle reaches its own player's channel (player * 2 + side). */
#include "../../runtime/hid_feedback.c"
#include <assert.h>

_Noreturn void runtime_fail(const char *why) { fprintf(stderr, "FAIL: %s\n", why); exit(1); }
void runtime_motion_start(void) {}
int runtime_motion_read(RuntimeMotionState *out, int max) { (void)out; (void)max; return 0; }
static int got = -1;
static float got_amp;
void runtime_rumble_set(unsigned channel, float al, float fl, float ah, float fh) { (void)fl; (void)ah; (void)fh; got = (int)channel; got_amp = al; }

static int send(uint32_t style, uint32_t id, uint32_t side, float amp) {
    uint32_t h = style | id << 8 | side << 16; got = -1;
    vib_send(&h, &(VibrationValue){amp, 160, 0, 320});
    return got;
}

int main(void) {
    assert(send(STYLE_FULL_KEY, 0, 0, .5f) == 0 && got_amp == .5f);   /* player 1, left */
    assert(send(STYLE_FULL_KEY, 0, 1, .5f) == 1);                     /* player 1, right */
    assert(send(STYLE_FULL_KEY, 1, 0, .7f) == 2 && got_amp == .7f);   /* player 2 rumbles player 2's controller */
    assert(send(STYLE_FULL_KEY, 7, 1, .5f) == 15);                    /* player 8, right */
    assert(send(STYLE_HANDHELD, 0x20, 1, .5f) == 1);                  /* handheld is player 1 */
    assert(send(STYLE_FULL_KEY, 8, 0, .5f) == -1);                    /* no such player: ignored */
    VibrationValue v; uint32_t h = STYLE_FULL_KEY | 1 << 8;
    vib_actual(&v, &h); assert(v.amp_low == .7f);                      /* each player keeps its own last value */
    h = STYLE_FULL_KEY; vib_actual(&v, &h); assert(v.amp_low == .5f);
    VibrationDeviceInfo info; h = STYLE_FULL_KEY | 3 << 8 | 1 << 16; vib_info(&info, &h); assert(info.position == 2);
    puts("rumble per player ok");
    return 0;
}
