/* nn::album through the host: a snapshot saved with user data is listed with its date, loads back at full size and as a
   thumbnail with the same pixels and user data, and the created time is packed like the SDK's; no replay videos. */
#include "../../runtime/album_host.c"
#include <assert.h>

_Noreturn void runtime_fail(const char *why) { fprintf(stderr, "FAIL: %s\n", why); exit(1); }
static void *need(const char *name) { void *f = runtime_album_override(name); assert(f); return f; }

int main(void) {
    uint32_t (*save)(const void *, size_t, int, int, int, const void *, size_t) = need("_ZN2nn5album26SaveScreenshotWithUserDataEPKvmNS0_9ImageSizeENS0_16ImageOrientationENS0_17AlbumReportOptionES2_m");
    uint32_t (*list)(int *, Entry *, int) = need("_ZN2nn5album26GetAlbumScreenshotFileListEPiPNS0_14AlbumFileEntryEi");
    uint32_t (*movies)(int *, Entry *, int) = need("_ZN2nn5album21GetExtraMovieFileListEPiPNS0_14AlbumFileEntryEi");
    uint32_t (*image)(int *, int *, uint64_t *, void *, size_t, void *, size_t, void *, size_t, const Entry *) = need("_ZN2nn5album14LoadAlbumImageEPiS1_PmPvmS3_mS3_mRKNS0_14AlbumFileEntryE");
    uint32_t (*thumb)(int *, int *, uint8_t *, void *, size_t, void *, size_t, const Entry *) = need("_ZN2nn5album23LoadAlbumThumbnailImageEPiS1_PNS0_18AlbumFileAttributeEPvmS4_mRKNS0_14AlbumFileEntryE");
    uint64_t (*created)(const Entry *) = need("_ZN2nn5album23GetAlbumFileCreatedTimeERKNS0_14AlbumFileEntryE");
    ((void (*)(void))need("_ZN2nn5album10InitializeEv"))();

    static uint8_t px[W * H * 4], back[W * H * 4], small[TW * TH * 4];
    for (int i = 0; i < W * H; i++) { px[4 * i] = 200; px[4 * i + 1] = 40; px[4 * i + 2] = 90; px[4 * i + 3] = 255; }
    const char user[] = "smash snapshot user data";
    int n = -1; Entry e[4];
    assert(!movies(&n, e, 4) && n == 0);
    assert(!list(&n, e, 4) && n == 0);
    assert(save(px, sizeof px, 0, 0, 3, user, sizeof user) == 0);
    assert(save(px, sizeof px - 4, 0, 0, 3, NULL, 0) == R_ALBUM);   /* a short picture is refused */
    assert(!list(&n, e, 4) && n == 1 && e[0].year >= 2026 && e[0].size > 0);
    uint64_t t = created(&e[0]); assert((t & 0xffff) == e[0].year && (t >> 16 & 0xff) == e[0].month && (t >> 48 & 0xff) == e[0].second);
    int w = 0, h = 0; uint64_t us = 0; char ub[0x200] = {0};
    assert(!image(&w, &h, &us, ub, sizeof ub, back, sizeof back, NULL, 0, &e[0]) && w == W && h == H && us == sizeof user && !strcmp(ub, user));
    for (int i = 0; i < W * H; i += 997) assert(abs(back[4 * i] - 200) < 8 && abs(back[4 * i + 1] - 40) < 8 && abs(back[4 * i + 2] - 90) < 8 && back[4 * i + 3] == 255);
    uint8_t attr[16]; memset(attr, 0xee, sizeof attr);
    assert(!thumb(&w, &h, attr, small, sizeof small, NULL, 0, &e[0]) && w == TW && h == TH && !attr[0] && !attr[15]);
    assert(abs(small[4 * (TW * 90 + 160)] - 200) < 8);
    assert(thumb(&w, &h, attr, small, sizeof small - 4, NULL, 0, &e[0]) == R_ALBUM);   /* too small for a thumbnail */
    puts("album round trip ok");
    return 0;
}
