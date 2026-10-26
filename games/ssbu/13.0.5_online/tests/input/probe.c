/* Local multiplayer (input.c): players 2-8 are Npad ids 1-7, one Mac controller each, apart from player 1. */
#include "../../runtime/input.c"
#include <assert.h>

_Noreturn void runtime_fail(const char *why) { fprintf(stderr, "FAIL: %s\n", why); exit(1); }
uint64_t runtime_now_ns(void) { return 1; }
int runtime_handheld_mode(void) { return 0; }
void runtime_survey_cancel_user(void) {}
void runtime_autoload_user_input(void) {}
int runtime_output_mode_cycle(void) { return 0; }
int runtime_aspect_toggle(void) { return 0; }
void runtime_shortcut_help_toggle(void) {}
void dm_gfx_set_flag(const char *name, int v) { (void)name; (void)v; }
void dm_gfx_shot_at(long frames, const char *path) { (void)frames; (void)path; }
void runtime_front_request_close(void) {}
void runtime_gamepad_clear(void) {}
void *runtime_hid_feedback_override(const char *name) { (void)name; return NULL; }
static RuntimeGamepadState fake[8];   /* index 0 = player 1's merged controller, 1.. = players 2.. */
void runtime_gamepad_read(RuntimeGamepadState *out) { *out = fake[0]; }
int runtime_gamepad_read_player(int player, RuntimeGamepadState *out) {
    *out = player > 0 && player < 8 ? fake[player] : (RuntimeGamepadState){0};
    return out->connected;
}

int main(void) {
    const uint32_t ids[] = {0, 1, 2, 3, 4, 5, 6, 7, 0x20}, one = 1, two = 2, zero = 0;
    initialize(); supported_styles(0x1f); supported_ids(ids, 9);
    fake[0] = (RuntimeGamepadState){.buttons = 1, .connected = 1};   /* player 1 holds A */
    fake[1] = (RuntimeGamepadState){.buttons = 2, .lx = -30000, .connected = 1};   /* player 2 holds B, stick left */
    assert(style(&zero) == 1 && style(&one) == 1 && style(&two) == 0);   /* player 3 has no controller */
    NpadState s;
    full_states(&s, 1, &one);
    assert(s.attributes == 1 && s.buttons == (2 | 1ull << 16) && s.lx == -30000);   /* B and the stick-left bit */
    full_states(&s, 1, &zero);
    assert(s.attributes == 1 && s.buttons == 1);   /* player 1 does not see player 2's buttons */
    full_states(&s, 1, &two);
    assert(s.attributes == 0 && s.buttons == 0);
    handheld_states(&s, 1, &one);
    assert(s.attributes == 0);   /* other players are full-key controllers only */
    supported_ids(ids, 1);   /* negative control: the game supports player 1 only */
    assert(style(&one) == 0);
    full_states(&s, 1, &one);
    assert(s.attributes == 0);
    puts("local multiplayer ok");
    return 0;
}
