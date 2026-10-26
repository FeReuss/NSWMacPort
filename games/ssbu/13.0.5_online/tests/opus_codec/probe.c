/* SSBU's SDK 8 HardwareOpusDecoder path through the host: the 2-argument work size, the class's
   HardwareOpusDecoderType fields, a Reset request performed by the next decode, and the class Finalize. */
#include "../../runtime/opus_codec.c"
#include <assert.h>
#include <math.h>

_Noreturn void runtime_fail(const char *why) { fprintf(stderr, "FAIL: %s\n", why); exit(1); }
void dm_guest_write_prepare(void *addr, size_t n) { (void)addr; (void)n; }

static void *need(const char *name) { void *f = runtime_codec_override(name); assert(f); return f; }
static unsigned char packet[8 + 1500];
static size_t packet_size;

int main(void) {
    size_t (*work_size2)(int, int) = need("_ZN2nn5codec36GetHardwareOpusDecoderWorkBufferSizeEii");
    unsigned (*init)(void *, int, int, void *, size_t, int) =
        need("_ZN2nn5codec29InitializeHardwareOpusDecoderEPNS0_23HardwareOpusDecoderTypeEiiPvmi");
    unsigned (*dec)(void *, size_t *, int *, int16_t *, size_t, const void *, size_t) =
        need("_ZN2nn5codec33DecodeOpusInterleavedWithHardwareEPNS0_23HardwareOpusDecoderTypeEPmPiPsmPKvm");
    void (*class_fin)(void *) = need("_ZN2nn5codec19HardwareOpusDecoder8FinalizeEv");

    int err; OpusEncoder *e = opus_encoder_create(48000, 2, OPUS_APPLICATION_AUDIO, &err);
    assert(e && err == OPUS_OK);
    int16_t pcm[960 * 2];
    for (int i = 0; i < 960; i++) pcm[2 * i] = pcm[2 * i + 1] = (int16_t)(8000 * sin(i * 0.05));
    int n = opus_encode(e, pcm, 960, packet + 8, 1500); assert(n > 0);
    opus_uint32 range; opus_encoder_ctl(e, OPUS_GET_FINAL_RANGE(&range)); opus_encoder_destroy(e);
    for (int i = 0; i < 4; i++) { packet[i] = (unsigned char)((unsigned)n >> (24 - 8 * i)); packet[4 + i] = (unsigned char)(range >> (24 - 8 * i)); }
    packet_size = 8 + (size_t)n;

    size_t size = work_size2(48000, 2); assert(size);
    static unsigned char work[1 << 16]; assert(size <= sizeof work);
    unsigned char object[0x48]; memset(object, 0xa5, sizeof object);
    GuestType *t = (GuestType *)(object + 8);
    assert(init(t, 48000, 2, work, size, 0) == 0);
    assert(t->session && t->work == work && t->size == size && t->rate == 48000 && t->channels == 2 && !t->reset);

    int16_t first[960 * 2], again[960 * 2], fresh[960 * 2]; size_t used; int samples;
    assert(dec(t, &used, &samples, first, sizeof first, packet, packet_size) == 0 && used == packet_size && samples == 960);
    assert(dec(t, &used, &samples, again, sizeof again, packet, packet_size) == 0 && samples == 960);
    assert(memcmp(first, again, sizeof first));   /* negative control: the decoder state carries over */
    t->reset = 1;                                  /* what HardwareOpusDecoder::Reset does */
    assert(dec(t, &used, &samples, fresh, sizeof fresh, packet, packet_size) == 0 && samples == 960);
    assert(!t->reset && !memcmp(first, fresh, sizeof first));

    class_fin(object);
    assert(!t->session && !t->rate && !t->channels && decoder_count == 0);
    puts("SDK 8 hardware Opus decoder path ok");
    return 0;
}
