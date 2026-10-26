/* nn::oe::RequestToRelaunchApplication (applet_host.c): the process restarts itself in place. No game. */
#include "../../runtime/applet_host.c"
#include <assert.h>
#include <fcntl.h>
#include <unistd.h>

_Noreturn void runtime_fail(const char *why) { fprintf(stderr, "FAIL: %s\n", why); exit(1); }
int runtime_core_away(void) { return 1; }
void runtime_core_back(int *away) { *away = 0; }
void hle_bind_system_event(void *event, int autoclear) { (void)event; (void)autoclear; }
void hle_signal_system_event(void *event) { (void)event; }
uint64_t runtime_now_ns(void) { return 0; }

int main(int argc, char **argv) {
    assert(argc == 2 && !strcmp(argv[1], "arg"));   // the same arguments both times
    const char *first = getenv("PROBE_FIRST_PID");
    if (!first) {
        int fd = open("/dev/null", O_RDONLY); assert(fd >= 3);   // like a server socket: must not survive
        char pid[32]; snprintf(pid, sizeof pid, "%d", getpid()); setenv("PROBE_FIRST_PID", pid, 1);
        char f[16]; snprintf(f, sizeof f, "%d", fd); setenv("PROBE_FD", f, 1);
        void (*relaunch)(void) = (void (*)(void))runtime_applet_override("_ZN2nn2oe28RequestToRelaunchApplicationEv");
        assert(relaunch); relaunch();
        return 2;   // not reached when the restart works
    }
    assert(atoi(first) == getpid());                               // the launcher keeps waiting on the same process
    assert(fcntl(atoi(getenv("PROBE_FD")), F_GETFD) == -1);         // descriptors beyond stdio were closed
    puts("pass: restarted in place");
    return 0;
}
