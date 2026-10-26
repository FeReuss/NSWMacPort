#pragma once
#include <stdint.h>
#include <stddef.h>
#include <pthread.h>
typedef struct { uint64_t where; unsigned kind, symbol; int64_t addend; } Reloc;
typedef struct { const char *name; uint64_t value, size; unsigned defined, binding, type; } Symbol;
typedef struct { uint64_t ret, original; unsigned timer, arg; } Gate;
typedef struct {
 const char *name, *digest; size_t file_size, size;
 uint64_t text_end, ro_off, data_off, patch_off, key_off, init, ctor_flag, init_array, init_size;
 const Symbol *symbols; unsigned nsym; const Reloc *relocs; unsigned nreloc;
 const Gate *gates; unsigned ngate; unsigned *hash, hash_mask;
 uint8_t *base; unsigned relative, symbolic, cross, weak, deferred;
} Module;
extern pthread_key_t runtime_keys[8];
_Noreturn void runtime_fail(const char *message);
void *runtime_symbol(const char *name);
void *runtime_override(const char *name);
void *runtime_ro_override(const char *name);        /* loader.c: nn::ro for the NRO modules */
void *runtime_friends_override(const char *name);   /* friends_host.c */
void *runtime_mii_override(const char *name);       /* mii_host.c */
int runtime_module_data_contains(uint64_t address, uint64_t size);   /* loader.c: inside a static module's data/bss */
int runtime_oof_enabled(void);                      /* oof_client.m: an ssbu-oof server and account were given by the launcher */
uint64_t runtime_oof_pid(void);
uint32_t runtime_oof_refresh_token(void);
size_t runtime_oof_token(char *out, size_t cap);
/* Friends from the ssbu-oof server (standard v0.2 section 7), cached by oof_client.m; friends_host.c reads them. */
#define RUNTIME_OOF_FRIENDS_MAX 300
typedef struct { uint64_t pid; char name[33]; uint8_t online; } RuntimeOofFriend;
int runtime_oof_friends(RuntimeOofFriend *out, int max);   /* the cached friends (a stale copy starts a refresh) */
int runtime_oof_blocked(uint64_t *out, int max);           /* the cached blocked players' PIDs */
void runtime_friends_notify(uint32_t type, uint64_t pid);   /* friends_host.c: 1 list changed, 2 that friend's presence */
struct in_addr;
int runtime_oof_nat_check(struct in_addr *out);   /* oof_client.m: out[0], out[1] = nncs1, nncs2 from GET /v1/status */
int runtime_oof_trust_ok(void *sec_trust);        /* net_host.c: the ssbu-oof server certificate chain is trusted (system store or OOF_CA) */
void runtime_hle_stack_init(uint64_t tsd_offset);
void runtime_gpu_path_probe(const char *which);
void runtime_audio_probe(void);
void *runtime_hle_wrap(void *fn);
void runtime_hle_stack_set(uintptr_t top);
void *runtime_font_override(const char *name);
void *runtime_codec_override(const char *name);
void *runtime_movie_override(const char *name);   /* runtime/movie_host.c: movie::Decoder for VP9 cutscenes */
void *runtime_time_override(const char *name);
int runtime_ldn_active(void);                     /* ldn_host.c: a local-play network is up (access point or joined) */
void *runtime_ldn_override(const char *name);     /* runtime/ldn_host.c: nn::ldn local wireless over the LAN */
void *runtime_album_override(const char *name);   /* runtime/album_host.c: nn::album snapshots in <profile>/album */
void *runtime_applet_override(const char *name);
void *runtime_swkbd_override(const char *name);   /* runtime/swkbd_host.c: nn::swkbd text entry with the Mac keyboard */
void runtime_swkbd_probe(void);
void runtime_swkbd_selftest(void);
void runtime_dump_threads(void);   /* threads.c: every guest thread's wait state and guest return chain (TOTK_INPUT_FILE "threads") */   /* diagnostic "kbtest": show the text entry bar from a thread of its own */
void runtime_frame_step_pin(double ms);                  /* game_patches.c: pin the real-time frame step (diagnostics), 0 = real time */
void runtime_frame_step_update(uint64_t now_ns);
void runtime_thread_note_hle(unsigned index);            /* threads.c: the calling guest thread's last host call (hletrace) */
uint32_t runtime_thread_current_handle(void);            /* threads.c: handle of the calling guest thread, 0 on a host thread */
void *runtime_hle_wrap_named(void *fn, const char *name);/* hle_stack.c: runtime_hle_wrap with a name for diagnostics */
void runtime_hle_trace_set(int mode);                    /* hle_stack.c: 1 count host calls, 2 also log the first call of each, 0 stop and print */
void runtime_hle_describe(unsigned index, char *out, size_t cap);   /* hle_stack.c: readable name of a host-call wrapper */
/* Text entry bar in the game window (runtime/frontend.m, Mac): the player types on the keyboard, Return confirms (1, the text
   as UTF-8 in out), Escape cancels (0). The caller waits; give the virtual core up around it (runtime_core_away). */
typedef struct { const char *prompt, *initial; int max_units, min_units, secure, digits_only; uint32_t disabled; } RuntimeTextRequest;
int runtime_front_text_input(const RuntimeTextRequest *req, char *out, size_t out_size);
void runtime_front_text_test(const char *op, const char *text);   /* diagnostics: "type" text, "enter", "esc" into the open bar */
int runtime_core_away(void);          /* runtime/threads.c: a guest thread gives its virtual core up around a long host wait */
void runtime_core_back(int *away);
void runtime_save_probe(void);
int runtime_fs_save_busy(void);   /* runtime/fs_host.c: the game is writing save data (window close waits) */
uint8_t *runtime_module_base(const char *name);   /* runtime/loader.c: "main", "subsdk0", "sdk" (probes) */
int runtime_guest_text(uint64_t pc);   /* original ExeFS text only: safe to suspend without retaining host locks */
uint64_t runtime_module_permission(uint64_t address,uint64_t size,unsigned permission);
uint64_t runtime_module_lock_permission(uint64_t address,uint64_t size,unsigned mask,unsigned attribute);
int runtime_module_query(uint64_t address,uint64_t *base,uint64_t *size,unsigned *state,unsigned *permission,unsigned *attribute);
void runtime_oe_push_message(uint32_t message);
void hle_bind_system_event(void *event, int autoclear);
void hle_signal_system_event(void *event);   /* threads.c: signal a bound event from any thread */
uint64_t runtime_svc(unsigned num, uint64_t *regs, const char *module, uint64_t offset);
void runtime_gate(void);
void runtime_thread_init(void);
void runtime_dispatch(uint64_t ret, uint64_t *regs);

uint64_t runtime_image_bytes(void);

void runtime_fs_init(void);
void *runtime_fs_override(const char *name);
void runtime_fs_probe(void);

void *runtime_observer(const char *name);

void runtime_startup_checkpoint(uintptr_t caller);

uint64_t runtime_now_ns(void);
uint32_t svc_thread_main_init(void);
int svc_thread(int num,uint64_t *regs);

void *runtime_gfx_override(const char *name);
int runtime_gfx_fault(void *addr,int is_write,void *context);
/* A thread that reads guest memory it does not own (uniform snapshot copies) sets this around the read: a SIGBUS / SIGSEGV
   on that thread then returns 1 from sigsetjmp instead of taking the process down (loader.c signal handler). */
#include <setjmp.h>
extern __thread sigjmp_buf *runtime_fault_guard;
extern _Atomic uint64_t runtime_unmap_gen;                  /* services.c: bumped when guest pages are unmapped */
int runtime_guest_mapped(uint64_t addr, uint64_t size);     /* services.c: physical-alias / heap range still mapped */
void dm_guest_write_prepare(void *addr,size_t n);

void runtime_trace_guest(uint64_t fp,uint64_t lr);
uint64_t runtime_desired_language(void);   /* services.c: the game language as a Switch language code (8 bytes) */

void runtime_front_run(void(*callback)(void));
void runtime_boot_progress(const char *stage,uint64_t done,uint64_t total);
void runtime_boot_status(const char *stage,const char *detail);
void runtime_boot_count(const char *stage,uint64_t done,uint64_t total);   /* a boot step counted in items (shader import) */
/* True 2K (runtime/game_patches.c, Mac): TOTK_OUTPUT_RES=<w>x<h> sets the game's own output size. runtime_output_res gives it
   (0 when unset); runtime_output_res_extra_bytes is what the HeapDef overlay (scripts/make_output_res_overlay.py, same formula)
   adds to the texture heap and to Graphics, and so to the application pool (runtime/services.c). */
int runtime_output_res(unsigned *w,unsigned *h);
uint64_t runtime_output_res_extra_bytes(void);
int runtime_output_res_toggle(void);                    /* legacy two-state output size switch */
int runtime_output_mode_cycle(void);                    /* key 3: MetalFX 2K -> native 2K -> off */
uint64_t runtime_output_size_current(void);              /* configured canvas: width in low 32 bits, height in high 32 bits */
void runtime_warm_progress(uint64_t done,uint64_t total,uint64_t shaders,uint64_t shader_total,uint64_t pipelines,uint64_t pipeline_total);
void runtime_warm_done(void);
void runtime_game_slot_acquire(const char *mode);   /* runtime/game_slot.c: one game at a time */
void *runtime_front_override(const char *name);
int runtime_front_focus(void);
void runtime_shortcut_help_toggle(void);
int fps_overlay_toggle_hints(void);   /* shared key 1 toggle for FPS, shader and system-load overlays */
int runtime_aspect_toggle(void);   /* runtime/aspect_patch.h: key 4, 16:9 <-> ultrawide (Mac, 1.2.1 / 1.4.3); 0 where unavailable */
float runtime_aspect_now(void);     /* the ratio in force, 0 = the game's own 16:9 */
int hud_compact(void);   /* gfx/nvn/fps_overlay.c: the overlay lines go to the window's corner panel (TOTK_HUD=corners: in the picture) */
int hud_snapshot(int line, char *out, size_t n, int *highlight);   /* line 0..2: FPS, shaders, load; 0 = not published */

void *runtime_account_override(const char *name);

void runtime_gfx_probe(int negative);
void runtime_vpool_probe(void);

void runtime_memory_probe(void);

void runtime_input_key(unsigned code,int down);
void runtime_input_clear(void);
int runtime_handheld_mode(void);   /* TOTK_OPERATION_MODE=handheld (runtime/services.c) */
void *runtime_input_override(const char *name);
void *runtime_net_override(const char *name);   /* runtime/net_host.c: nifm, nsd, nn::socket, nn::ssl (ssbu-oof standard 3-6) */
void *runtime_by3_override(const char *name);   /* runtime/system_services.c: Bayonetta 3 network and system functions */
typedef struct { uint64_t buttons; int32_t lx,ly,rx,ry; int connected; } RuntimeGamepadState;
void runtime_gamepad_start(void);
void runtime_gamepad_read(RuntimeGamepadState *out);
int runtime_gamepad_read_player(int player, RuntimeGamepadState *out);   /* players 2-8 (index 1-7), gamepad.m; 0 when absent */
/* Forget held buttons and sticks: macOS stops delivering controller events (including releases) while the app is in
   the background, so a button held at focus loss would otherwise stay pressed for the game. */
void runtime_gamepad_clear(void);
void runtime_touch_pad_set(const RuntimeGamepadState *state);   /* on-screen touch controls (iOS) */
/* Controller rumble and motion (opt-in TOTK_RUMBLE=1 / TOTK_GYRO=1): the game's hid side in runtime/hid_feedback.c, the
   Mac controller's haptics and motion sensors in runtime/gamepad_feedback.m. */
#define RUNTIME_MOTION_MAX 32
typedef struct { uint64_t sampling, delta_ns; float acc[3], gyro[3], angle[3], dir[9]; } RuntimeMotionState;   /* G, rev/s, rev, 3 axes */
int runtime_rumble_enabled(void);
int runtime_gyro_enabled(void);
void *runtime_hid_feedback_override(const char *name);
int runtime_hid_feedback_flag(const char *name, int v);   /* "flag rumble|gyro 0|1"; 0 = not one of these names */
void runtime_hid_feedback_stats(uint64_t *values, uint64_t *motion_reads);
#define RUNTIME_RUMBLE_PLAYERS 8   /* one rumble channel per player and motor: player * 2 + side (0 left, 1 right) */
void runtime_rumble_set(unsigned channel, float amp_low, float freq_low, float amp_high, float freq_high);
int runtime_motion_read(RuntimeMotionState *out, int max);   /* newest first; 0 = no motion now */
void runtime_motion_start(void);   /* the game (re)started its sensor: angle and direction start over */
void runtime_motion_feed(const double acc_g[3], const double gyro_rad_s[3], uint64_t now_ns);   /* runtime/motion_fusion.c */
int runtime_motion_axes(const char *spec);   /* TOTK_GYRO_AXES ("-x,y,-z"): 1 set, 0 unset, -1 malformed */
void runtime_motion_deadzone_default(double deg_s);   /* when TOTK_GYRO_DEADZONE is unset: 1.2 for HID motion, else 0 */
void runtime_motion_live(int on);   /* a controller with motion attached (1) / detached (0) */
void runtime_feedback_quiet(void);   /* focus lost: stop rumbling */
#ifdef __OBJC__
@class GCController;
void runtime_feedback_attach(GCController *controller, int player);   /* player 0-7 (gamepad.m slot), -1: none */
void runtime_feedback_detach(GCController *controller);
#endif
