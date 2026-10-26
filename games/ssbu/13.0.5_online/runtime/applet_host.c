/* Host side of applet, telemetry, NFC and error-display services that main reaches through IPC.
   Client-side SDK code (PlayReport encoding, keyboard presets, diag logging) runs unchanged; only the
   calls that would talk to system services are taken here. */
#include "runtime.h"
#include <crt_externs.h>
#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <pthread.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---------- nn::oe ---------- */
static float balance[2] = {1.0f, 1.0f};
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t message_cv = PTHREAD_COND_INITIALIZER;
static uint32_t messages[32]; static unsigned head, tail;
static uint64_t start_ns;
__attribute__((constructor)) static void started(void) { start_ns = runtime_now_ns(); }

/* SDK 8 (SSBU 13.0.5): a thread waits on this event, then calls TryPopNotificationMessage (main+0x3754f38). It is signaled
   while a message is queued, as the applet manager's message event is; the kernel event behind it is made by the SDK's
   own CreateSystemEvent on the first guest call (hle_bind_system_event). The struct size covers SDK 8's SystemEventType. */
static _Alignas(16) uint8_t message_event[0x100];
static _Atomic int message_event_bound;
void runtime_oe_push_message(uint32_t message) {
    pthread_mutex_lock(&lock);
    if (tail - head < 32) { messages[tail++ % 32] = message; pthread_cond_broadcast(&message_cv); }
    pthread_mutex_unlock(&lock);
    if (message_event_bound) hle_signal_system_event(message_event);
}
static void *message_event_get(void) {
    if (!message_event_bound) {
        hle_bind_system_event(message_event, 1); message_event_bound = 1;
        pthread_mutex_lock(&lock); int pending = head != tail; pthread_mutex_unlock(&lock);
        if (pending) hle_signal_system_event(message_event);
    }
    return message_event;
}
static void get_balance(float *l, float *r) { pthread_mutex_lock(&lock); if (l) *l = balance[0]; if (r) *r = balance[1]; pthread_mutex_unlock(&lock); }
static void set_balance(float l, float r) {
    if (l < 0 || l > 1 || r < 0 || r > 1) runtime_fail("volume balance out of range");
    pthread_mutex_lock(&lock); balance[0] = l; balance[1] = r; pthread_mutex_unlock(&lock);
}
static int64_t total_active_time(void) { return (int64_t)(runtime_now_ns() - start_ns); }   /* TimeSpan in ns */
/* The host never sleeps the console; the extended inactivity window has no further effect. */
static void inactivity_extended(unsigned char on) { (void)on; }
/* No system startup logo is drawn on the host, so there is nothing to dismiss. */
static void finish_startup_logo(void) { fprintf(stderr, "[totk] game finished startup logo\n"); }
/* The single native executable was launched directly; it has no predecessor
 * in an applet ExecuteProgram chain. -1 keeps the game's normal user-open path. */
static int previous_program_index(void) { return -1; }
/* A stable, nonzero resource id for this process's one application. Host input
 * and audio share that application; there is no separate system applet client. */
static uint64_t applet_resource_user_id(void) { return 1; }
static unsigned char try_pop_launch_parameter(uint64_t *size,void *buffer,uint64_t capacity) {
    (void)buffer;(void)capacity;if(size)*size=0;return 0;
}
static unsigned char try_pop(uint32_t *out) {
    pthread_mutex_lock(&lock); int ok = head != tail; if (ok && out) *out = messages[head++ % 32]; int more = head != tail; pthread_mutex_unlock(&lock);
    if (more && message_event_bound) hle_signal_system_event(message_event);   /* the event clears on wake: keep it set while messages remain */
    return (unsigned char)ok;
}
int runtime_core_away(void); void runtime_core_back(int *away);
static uint32_t pop(void) {
    __attribute__((cleanup(runtime_core_back))) int away = runtime_core_away();   /* blocks like a Horizon wait */
    pthread_mutex_lock(&lock); while (head == tail) pthread_cond_wait(&message_cv, &lock);
    uint32_t m = messages[head++ % 32]; pthread_mutex_unlock(&lock); return m;
}

/* SDK 8 (SSBU 13.0.5) applet controls with no host counterpart: screenshots are the host's, the display never sleeps while
   the game is visible (frontend), the backlight and TV power belong to the Mac, and closing the window has its own path. */
static void oe_nothing(void) {}
static void oe_tv_power_mode(int mode) { (void)mode; }
/* The game asks to be restarted, as the console's system would: the process becomes a fresh copy of itself (same pid, so
   the launcher keeps waiting on it; same arguments and environment). Only stdin/stdout/stderr survive: sockets to the
   server and the game slot lock must not outlive the old game. If that fails the game ends cleanly as before. */
static void oe_relaunch(void) {
    fprintf(stderr, "[totk] the game asked to be restarted: starting it again\n"); fflush(stderr);
    char path[PATH_MAX]; uint32_t n = sizeof path;
    posix_spawn_file_actions_t fa; posix_spawn_file_actions_init(&fa);
    for (int fd = 0; fd < 3; fd++) posix_spawn_file_actions_addinherit_np(&fa, fd);
    posix_spawnattr_t sa; posix_spawnattr_init(&sa); posix_spawnattr_setflags(&sa, POSIX_SPAWN_SETEXEC | POSIX_SPAWN_CLOEXEC_DEFAULT);
    if (!_NSGetExecutablePath(path, &n)) posix_spawn(NULL, path, &fa, &sa, *_NSGetArgv(), *_NSGetEnviron());
    fprintf(stderr, "[totk] restart failed (%s); start the game again\n", strerror(errno)); exit(0);
}
/* ---------- nn::prepo: play reports are not transmitted (no Nintendo servers) ---------- */
static uint64_t reports;
static unsigned report_save(const void *report) { (void)report; if (!(reports++ % 100)) fprintf(stderr, "[totk] play report kept local (not transmitted), count %llu\n", (unsigned long long)reports); return 0; }
static unsigned report_save_uid(const void *report, const void *uid) { (void)uid; return report_save(report); }
static unsigned immediate_transmission(void) { return 0; }
static unsigned connection_status(void *report, const char *a, const char *b, const char *c) { (void)report; (void)a; (void)b; (void)c; return 0; }

/* ---------- nn::ngc: the profanity filter loads the system's word list (system data through fsp-srv), which the host does
   not have; SSBU checks arena names and comments with it. Nothing is masked and the filter stays "not initialized", so
   the SDK's destructor unmounts nothing (it tests the flag at +9).
   shortcut: names are shown as typed; moderate them on the ssbu-oof server if a server operator needs it ---------- */
static uint32_t ngc_initialize(void *filter, void *work, unsigned long size) { (void)filter; (void)work; (void)size; return 0; }
static uint32_t ngc_mask(void *filter, int *count, uint16_t *text, unsigned flags) { (void)filter; (void)text; (void)flags; if (count) *count = 0; return 0; }

/* ---------- nn::nfp: the host has no NFC reader ---------- */
#define NFP_DEVICE_NOT_FOUND 0x8073u   /* nfp module 115, description 64 */
static int nfp_state;
static unsigned nfp_initialize(void) { nfp_state = 1; return 0; }
static unsigned nfp_finalize(void) { nfp_state = 0; return 0; }
static int nfp_get_state(void) { return nfp_state; }
static unsigned nfp_list(void *handles, int *count, int max) { (void)handles; (void)max; if (count) *count = 0; return NFP_DEVICE_NOT_FOUND; }
static unsigned nfp_attach_availability(void *event) { if (!event) runtime_fail("nfp availability event"); hle_bind_system_event(event, 0); return 0; }
static unsigned nfp_no_device(void) { return NFP_DEVICE_NOT_FOUND; }
static int nfp_device_state(const void *h) { (void)h; return 0; }

/* ---------- nn::err (nn::swkbd is in runtime/swkbd_host.c) ---------- */
static void show_error(uint32_t result) {
    fprintf(stderr, "[totk] game error dialog: %04u-%04u (result %#x)\n", 2000 + (result & 0x1ff), (result >> 9) & 0x1fff, result);
}

/* ---------- nn::web: the host has no browser applet ----------
   Layouts from the 1.3.0 sdk: ShowOfflineHtmlPageArg starts with a 0x2000-byte list (u16 count at +0, entries from +8,
   each u16 kind, u16 size, u32 0, then size bytes; the document path is kind 6, SetDocumentPath at sdk+0x3b5f10).
   OfflineHtmlPageReturnValue is 0x1010 bytes: exit reason at +0 (constructor stores 20, GetOfflineExitReason reads a
   32-bit value), last URL at +8 and its size at +0x1008. main's one caller (main+0x6cb5b4) treats a nonzero Result as
   failure and reads the last URL only for exit reason 3, so the page reports success, closed by the player (1). */
#define WEB_ARG_SIZE 0x2000u
#define WEB_DOCUMENT_PATH 6
#define WEB_EXIT_BACK_BUTTON 1
static unsigned show_offline_html_page(void *out, const void *arg) {
    const uint8_t *a = arg; const char *path = NULL; int path_len = 0;
    unsigned count = a ? (unsigned)(a[0] | a[1] << 8) : 0;
    for (size_t at = 8; count-- && at + 8 <= WEB_ARG_SIZE;) {
        unsigned kind = a[at] | a[at + 1] << 8, size = a[at + 2] | a[at + 3] << 8;
        if (at + 8 + size > WEB_ARG_SIZE) break;
        if (kind == WEB_DOCUMENT_PATH) { path = (const char *)a + at + 8; path_len = (int)strnlen(path, size); break; }
        at += 8 + size;
    }
    fprintf(stderr, "[totk] offline web page \"%.*s\" requested; no browser applet on the host, reported closed\n", path_len, path ? path : "");
    if (!out) runtime_fail("offline web page without a return value");
    uint8_t *r = out; memset(r, 0, 0x1010); uint64_t reason = WEB_EXIT_BACK_BUTTON; memcpy(r, &reason, sizeof reason);
    return 0;
}

/* ---------- nn::ec: the host has no Nintendo eShop ----------
   The SDK would open the shop applet or the shop service over IPC, which the host does not have (the game stopped with
   "service not implemented"). A shop page reports itself opened and closed by the player; the shop service fails with
   the nn::ec result the SDK itself gives a response that was never requested (AsyncResponse::Get, sdk+0x18e1f8), and
   leaves the response unrequested, so the SDK's TryWait/Get/GetSize then answer without IPC. */
#define EC_RESULT_NO_SHOP 0x190a4u
static unsigned show_shop(void) { fprintf(stderr, "[totk] Nintendo eShop page requested; no shop on the host, reported closed\n"); return 0; }
static unsigned shop_service_unavailable(void) { return EC_RESULT_NO_SHOP; }
static unsigned shop_nothing(void) { return 0; }

void *runtime_applet_override(const char *name) {
    static const struct { const char *name; void *fn; } map[] = {
        {"_ZN2nn6applet23GetPreviousProgramIndexEv", previous_program_index},
        {"_ZN2nn6applet23GetAppletResourceUserIdEv", applet_resource_user_id},
        {"_ZN2nn2am23GetAppletResourceUserIdEv", applet_resource_user_id},
        {"_ZN2nn2oe21TryPopLaunchParameterEPmPvm", try_pop_launch_parameter},
        {"_ZN2nn2oe24GetExpectedVolumeBalanceEPfS1_", get_balance}, {"_ZN2nn2oe24SetExpectedVolumeBalanceEff", set_balance},
        {"_ZN2nn2oe25GetProgramTotalActiveTimeEv", total_active_time}, {"_ZN2nn2oe38SetUserInactivityDetectionTimeExtendedEb", inactivity_extended},
        {"_ZN2nn2oe44SetUserInactivityDetectionTimeExtendedUnsafeEb", inactivity_extended},
        {"_ZN2nn2oe17FinishStartupLogoEv", finish_startup_logo}, {"_ZN2nn2oe25TryPopNotificationMessageEPj", try_pop},
        {"_ZN2nn2oe22PopNotificationMessageEv", pop},
        {"_ZN2nn2oe27GetNotificationMessageEventEv", message_event_get},
        {"_ZN2nn2oe16EnableScreenShotEv", oe_nothing}, {"_ZN2nn2oe17DisableScreenShotEv", oe_nothing},
        {"_ZN2nn2oe20SwitchLcdBacklightOnEv", oe_nothing}, {"_ZN2nn2oe21SwitchLcdBacklightOffEv", oe_nothing},
        {"_ZN2nn2oe29BeginAutoSleepDisabledSectionEv", oe_nothing}, {"_ZN2nn2oe27EndAutoSleepDisabledSectionEv", oe_nothing},
        {"_ZN2nn2oe31EnterExitRequestHandlingSectionEv", oe_nothing}, {"_ZN2nn2oe31LeaveExitRequestHandlingSectionEv", oe_nothing},
        {"_ZN2nn2oe27SetTvPowerStateMatchingModeENS0_24TvPowerStateMatchingModeE", oe_tv_power_mode},
        {"_ZN2nn2oe28RequestToRelaunchApplicationEv", oe_relaunch},
        {"_ZN2nn5prepo10PlayReport4SaveEv", report_save}, {"_ZN2nn5prepo10PlayReport4SaveERKNS_7account3UidE", report_save_uid},
        {"_ZN2nn5prepo28RequestImmediateTransmissionEv", immediate_transmission},
        {"_ZN2nn5prepo27AddInternetConnectionStatusEPNS0_10PlayReportEPKcS4_S4_", connection_status},
        {"_ZN2nn3nfp10InitializeEv", nfp_initialize}, {"_ZN2nn3nfp8FinalizeEv", nfp_finalize}, {"_ZN2nn3nfp8GetStateEv", nfp_get_state},
        {"_ZN2nn3nfp11ListDevicesEPNS0_12DeviceHandleEPii", nfp_list},
        {"_ZN2nn3nfp29AttachAvailabilityChangeEventEPNS_2os15SystemEventTypeE", nfp_attach_availability},
        {"_ZN2nn3nfp14GetDeviceStateERKNS0_12DeviceHandleE", nfp_device_state},
        {"_ZN2nn3nfp5MountERKNS0_12DeviceHandleENS0_9ModelTypeENS0_11MountTargetE", nfp_no_device},
        {"_ZN2nn3nfp9GetNpadIdEPjRKNS0_12DeviceHandleE", nfp_no_device},
        {"_ZN2nn3nfp19AttachActivateEventEPNS_2os15SystemEventTypeERKNS0_12DeviceHandleE", nfp_no_device},
        {"_ZN2nn3nfp21AttachDeactivateEventEPNS_2os15SystemEventTypeERKNS0_12DeviceHandleE", nfp_no_device},
        {"_ZN2nn3nfp14StartDetectionERKNS0_12DeviceHandleE", nfp_no_device}, {"_ZN2nn3nfp13StopDetectionERKNS0_12DeviceHandleE", nfp_no_device},
        {"_ZN2nn3nfp10GetTagInfoEPNS0_7TagInfoERKNS0_12DeviceHandleE", nfp_no_device},
        {"_ZN2nn3nfp12GetModelInfoEPNS0_9ModelInfoERKNS0_12DeviceHandleE", nfp_no_device},
        {"_ZN2nn3err9ShowErrorENS_6ResultE", show_error},
        {"_ZN2nn3ngc15ProfanityFilter10InitializeEPvm", ngc_initialize},
        {"_ZN2nn3ngc15ProfanityFilter24MaskProfanityWordsInTextEPiPtj", ngc_mask},
        {"_ZN2nn3web19ShowOfflineHtmlPageEPNS0_26OfflineHtmlPageReturnValueERKNS0_22ShowOfflineHtmlPageArgE", show_offline_html_page},
        {"_ZN2nn2ec19ShowShopProductListEPKNS0_5NsUidEiPKcRKNS_7account10UserHandleE", show_shop},
        {"_ZN2nn2ec22ShowShopProductDetailsENS0_5NsUidERKNS_7account10UserHandleE", show_shop},
        {"_ZN2nn2ec24ShowShopAddOnContentListENS_13ApplicationIdE", show_shop},
        {"_ZN2nn2ec24ShowShopAddOnContentListENS_13ApplicationIdERKNS_7account10UserHandleE", show_shop},
        {"_ZN2nn2ec33InitializeForShopServiceAccessorsEPvm", shop_nothing}, {"_ZN2nn2ec31FinalizeForShopServiceAccessorsEv", shop_nothing},
        {"_ZN2nn2ec19ShopServiceAccessor10InitializeERKNS0_11ShopServiceE", shop_service_unavailable},
        {"_ZN2nn2ec19ShopServiceAccessor7RequestEPNS1_13AsyncResponseERKNS_7account3UidENS0_11ShopService6MethodEPKcRKNS1_8PostDataENS_8TimeSpanE", shop_service_unavailable},
    };
    for (size_t i = 0; i < sizeof map / sizeof *map; i++) if (!strcmp(name, map[i].name)) return map[i].fn;
    return NULL;
}
