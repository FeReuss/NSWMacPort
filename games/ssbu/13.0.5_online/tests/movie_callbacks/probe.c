/* Every stand-in guest callback rejects being called with a movie host mutex held. */
#include "../../runtime/movie_host.c"
#include <assert.h>

typedef struct { uint8_t *data; size_t capacity, size; } Buffer;
static Movie *watched;
static unsigned callbacks;
static uint8_t csd_bytes[] = {1, 0, 0, 0, 0, 0, 0, 0};
static Buffer csd_buffer = {csd_bytes, sizeof csd_bytes, sizeof csd_bytes};
_Noreturn void runtime_fail(const char *why) { fprintf(stderr, "FAIL: %s\n", why); exit(1); }
void *runtime_symbol(const char *name) { (void)name; runtime_fail("unexpected guest symbol lookup"); }
static void guest_call(void) {
    assert(pthread_mutex_trylock(&g_lock) == 0); pthread_mutex_unlock(&g_lock);
    if (watched) { assert(pthread_mutex_trylock(&watched->mu) == 0); pthread_mutex_unlock(&watched->mu); }
    callbacks++;
}
static void range(void *p, int offset, int size) { guest_call(); assert(offset == 0); ((Buffer *)p)->size = (size_t)size; }
static void *base(const void *p) { guest_call(); return ((const Buffer *)p)->data; }
static size_t capacity(const void *p) { guest_call(); return ((const Buffer *)p)->capacity; }
static size_t size(const void *p) { guest_call(); return ((const Buffer *)p)->size; }
static bool find_string(const void *p, const char *key, const char **out) {
    (void)p; guest_call(); assert(!strcmp(key, "mime")); *out = "video/avc"; return true;
}
static bool find_int(const void *p, const char *key, int *out) {
    (void)p; guest_call(); assert(!strcmp(key, "width") || !strcmp(key, "height")); *out = 4; return true;
}
static bool find_buffer(const void *p, const char *key, void **out) {
    (void)p; guest_call(); if (strcmp(key, "avcc")) return false; *out = &csd_buffer; return true;
}
static void set_string(void *p, const char *key, const char *value) { (void)p; (void)key; (void)value; guest_call(); }
static void set_int(void *p, const char *key, int value) { (void)p; (void)key; (void)value; guest_call(); }
static void set_rect(void *p, const char *key, int a, int b, int c, int d) {
    (void)p; (void)key; (void)a; (void)b; (void)c; (void)d; guest_call();
}
int main(void) {
    MovieGuest api = {.buf_set_range = range, .buf_base = base, .buf_capacity = capacity,
        .md_find_int32 = find_int, .md_find_string = find_string, .md_find_buffer = find_buffer,
        .buf_data = base, .buf_size = size, .md_set_string = set_string,
        .md_set_int32 = set_int, .md_set_rect = set_rect};
    movie_host_set_guest(&api);
    void *obj[2] = {0}; int md;
    h_ctor(obj, TYPE_AVC, FORMAT_NV12, MODE_BUFFER, 44); watched = find(obj);
    assert(h_configure(obj, &md) == ST_OK);
    assert(h_get_output_format(obj, &md) == ST_OK);
    uint8_t bytes[16] = {0}; Buffer out = {bytes, sizeof bytes, 0};
    memcpy(watched->out[0], "frame!", 6); watched->out_bytes[0] = 6; watched->out_held[0] = 1;
    assert(h_get_output(obj, 0, &out) == ST_OK && out.size == 6 && !memcmp(bytes, "frame!", 6));
    out.capacity = 3;
    assert(h_get_output(obj, 0, &out) == ST_ERROR && out.size == 0);
    watched->out_bytes[0] = 0;
    assert(h_get_output(obj, 0, &out) == ST_OK && out.size == 0);
    assert(h_get_output(obj, -1, &out) == ST_ERROR && out.size == 0);
    assert(h_get_output(obj, 1, &out) == ST_ERROR && out.size == 0);
    assert(callbacks >= 25);
    watched = NULL; h_d2(obj);
    puts("pass: guest callbacks run without movie locks; output, short buffer, EOS and invalid slots preserved");
    return 0;
}
