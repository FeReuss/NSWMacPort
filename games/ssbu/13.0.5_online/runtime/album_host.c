/* nn::album (SDK 8.2.1 application API) on the host. The console keeps the pictures a game saves in its own album behind
   caps:su / caps:u, which the host does not have: opening Vault > Replays (nn::album::Initialize, then
   GetExtraMovieFileList) closed the game with "IPC service not implemented" (2026-10-24). Here the album is a folder,
   <TOTK_PROFILE_DIR>/album: each snapshot Smash saves is a JPEG named by its date and time, with the game's user data
   beside it (.ud); the Vault's album lists and loads them back.
   AlbumFileEntry is 0x20 bytes the SDK copies whole (ConvertAlbumEntry, sdk+0x50a50); its date and time are at +0x10
   (GetAlbumFileCreatedTime, sdk+0x50974). Signatures follow main's calls (main+0x1ae7d48: user data size 0x200, picture
   0x384000 = 1280x720 RGBA, work 0x7d000).
   Replays turned into videos (MovieMaker) are not made on the host: that list stays empty and starting a conversion
   returns the album's generic error, which the game reports. shortcut: no video export; upgrade with an AVAssetWriter
   behind GetMovieMakerNativeWindow when someone needs replay videos. */
#include "runtime.h"
#include <CoreGraphics/CoreGraphics.h>
#include <ImageIO/ImageIO.h>
#include <dirent.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

typedef struct { uint64_t size, id; uint16_t year; uint8_t month, day, hour, minute, second, seq, storage, content, pad[6]; } Entry;
_Static_assert(sizeof(Entry) == 0x20, "nn::album::AlbumFileEntry");
#define R_ALBUM 0xcad6u   /* album 214-101: the error ConvertCapsrvResult gives for anything it does not name (sdk+0x50b78) */
enum { W = 1280, H = 720, TW = 320, TH = 180, UD_MAX = 0x400 };
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;

static void dir_path(char *out, size_t n) { const char *p = getenv("TOTK_PROFILE_DIR"); snprintf(out, n, "%s/album", p ? p : "user-data"); }
static void file_path(const Entry *e, const char *ext, char *out, size_t n) {
    char d[3072]; dir_path(d, sizeof d);
    snprintf(out, n, "%s/%04u%02u%02u%02u%02u%02u%02u.%s", d, e->year, e->month, e->day, e->hour, e->minute, e->second, e->seq, ext);
}
static bool from_name(const char *dir, const char *name, Entry *e) {
    unsigned y, mo, d, h, mi, s, q; char ext[8];
    if (strlen(name) != 20 || sscanf(name, "%4u%2u%2u%2u%2u%2u%2u.%3s", &y, &mo, &d, &h, &mi, &s, &q, ext) != 8 || strcmp(ext, "jpg")) return false;
    if (mo < 1 || mo > 12 || d < 1 || d > 31 || h > 23 || mi > 59 || s > 59) return false;
    char p[4096]; struct stat st; snprintf(p, sizeof p, "%s/%s", dir, name); if (stat(p, &st)) return false;
    *e = (Entry){.size = (uint64_t)st.st_size, .id = (uint64_t)q, .year = (uint16_t)y, .month = (uint8_t)mo, .day = (uint8_t)d,
                 .hour = (uint8_t)h, .minute = (uint8_t)mi, .second = (uint8_t)s, .seq = (uint8_t)q};
    return true;
}
static int by_date(const void *a, const void *b) { return memcmp(&((const Entry *)a)->year, &((const Entry *)b)->year, 8) > 0 ? 1 : -1; }

static void initialize(void) {}
/* Screenshots, oldest first (the console's order). */
static uint32_t screenshots(int *count, Entry *out, int max) {
    if (!count || (max > 0 && !out)) return R_ALBUM;
    char d[3072]; dir_path(d, sizeof d); int n = 0;
    pthread_mutex_lock(&mu); DIR *dp = opendir(d);
    if (dp) { struct dirent *de; Entry *all = NULL; int cap = 0, k = 0;
        while ((de = readdir(dp))) { Entry e; if (!from_name(d, de->d_name, &e)) continue;
            if (k == cap) { cap = cap ? cap * 2 : 64; Entry *grown = realloc(all, (size_t)cap * sizeof *all); if (!grown) break; all = grown; }
            all[k++] = e; }
        closedir(dp); qsort(all, (size_t)k, sizeof *all, by_date);
        for (; n < k && n < max; n++) out[n] = all[n];
        free(all); }
    pthread_mutex_unlock(&mu); *count = n; return 0;
}
static uint32_t no_movies(int *count, Entry *out, int max) { (void)out; (void)max; if (!count) return R_ALBUM; *count = 0; return 0; }

static uint32_t save(const void *image, size_t size, int image_size, const void *user, size_t user_size) {
    if (!image || image_size != 0 || size < (size_t)W * H * 4 || user_size > UD_MAX || (user_size && !user)) return R_ALBUM;
    char d[3072]; dir_path(d, sizeof d); mkdir(d, 0755);
    time_t now = time(NULL); struct tm t; localtime_r(&now, &t);
    Entry e = {.year = (uint16_t)(t.tm_year + 1900), .month = (uint8_t)(t.tm_mon + 1), .day = (uint8_t)t.tm_mday,
               .hour = (uint8_t)t.tm_hour, .minute = (uint8_t)t.tm_min, .second = (uint8_t)(t.tm_sec > 59 ? 59 : t.tm_sec)};
    char path[4096], part[4200], ud[4096];
    pthread_mutex_lock(&mu);
    for (e.seq = 0; e.seq < 100; e.seq++) { file_path(&e, "jpg", path, sizeof path); if (access(path, F_OK)) break; }
    uint32_t r = R_ALBUM;
    if (e.seq < 100) {
        snprintf(part, sizeof part, "%s.partial", path); file_path(&e, "ud", ud, sizeof ud);
        CGColorSpaceRef cs = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
        CGDataProviderRef src = CGDataProviderCreateWithData(NULL, image, (size_t)W * H * 4, NULL);
        CGImageRef img = CGImageCreate(W, H, 8, 32, W * 4, cs, kCGImageAlphaNoneSkipLast | kCGBitmapByteOrderDefault, src, NULL, false, kCGRenderingIntentDefault);
        CFURLRef url = CFURLCreateFromFileSystemRepresentation(NULL, (const UInt8 *)part, (CFIndex)strlen(part), false);
        CGImageDestinationRef dst = url ? CGImageDestinationCreateWithURL(url, CFSTR("public.jpeg"), 1, NULL) : NULL;
        if (img && dst) {
            float q = 0.92f; CFNumberRef qn = CFNumberCreate(NULL, kCFNumberFloatType, &q);
            const void *k[] = {kCGImageDestinationLossyCompressionQuality}, *v[] = {qn};
            CFDictionaryRef opt = CFDictionaryCreate(NULL, k, v, 1, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
            CGImageDestinationAddImage(dst, img, opt); CFRelease(opt); CFRelease(qn);
            FILE *f = user_size ? fopen(ud, "wb") : NULL;
            bool ok = CGImageDestinationFinalize(dst) && (!user_size || (f && fwrite(user, 1, user_size, f) == user_size));
            if (f && fclose(f)) ok = false;
            if (ok && !rename(part, path)) r = 0; else { unlink(part); unlink(ud); }
        }
        if (dst) CFRelease(dst); if (url) CFRelease(url); if (img) CGImageRelease(img); CGDataProviderRelease(src); CGColorSpaceRelease(cs);
    }
    pthread_mutex_unlock(&mu);
    fprintf(stderr, "[album] screenshot %s: %s\n", r ? "not saved" : "saved", path);
    return r;
}
static uint32_t save_plain(const void *image, size_t size, int image_size, int report) { (void)report; return save(image, size, image_size, NULL, 0); }
static uint32_t save_user(const void *image, size_t size, int image_size, int orientation, int report, const void *user, size_t user_size) {
    (void)orientation; (void)report; return save(image, size, image_size, user, user_size);
}
/* Posting to social media is an online service: the picture is saved and nothing is shared. */
static uint32_t save_share(const void *image, size_t size, int image_size, int report, const void *uid, const char *text) {
    (void)report; (void)uid; (void)text; return save(image, size, image_size, NULL, 0);
}

/* The picture (or its 320x180 thumbnail) as RGBA8, and the user data saved with it. */
static uint32_t load(int *w, int *h, uint64_t *user_size, void *user, size_t user_cap, void *image, size_t image_size, const Entry *e, bool thumb) {
    int tw = thumb ? TW : W, th = thumb ? TH : H;
    if (!w || !h || !image || !e || image_size < (size_t)tw * th * 4 || (user_cap && !user)) return R_ALBUM;
    char path[4096], ud[4096]; file_path(e, "jpg", path, sizeof path); file_path(e, "ud", ud, sizeof ud);
    pthread_mutex_lock(&mu); uint32_t r = R_ALBUM;
    CFURLRef url = CFURLCreateFromFileSystemRepresentation(NULL, (const UInt8 *)path, (CFIndex)strlen(path), false);
    CGImageSourceRef src = url ? CGImageSourceCreateWithURL(url, NULL) : NULL;
    CGImageRef img = src ? CGImageSourceCreateImageAtIndex(src, 0, NULL) : NULL;
    if (img) {
        CGColorSpaceRef cs = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
        CGContextRef ctx = CGBitmapContextCreate(image, (size_t)tw, (size_t)th, 8, (size_t)tw * 4, cs, kCGImageAlphaPremultipliedLast | kCGBitmapByteOrderDefault);
        if (ctx) { CGContextSetInterpolationQuality(ctx, kCGInterpolationHigh); CGContextDrawImage(ctx, CGRectMake(0, 0, tw, th), img); CGContextRelease(ctx); r = 0; }
        CGColorSpaceRelease(cs); CGImageRelease(img);
    }
    if (src) CFRelease(src); if (url) CFRelease(url);
    if (!r && user_size) { size_t n = 0; FILE *f = fopen(ud, "rb"); if (f) { n = fread(user, 1, user_cap, f); fclose(f); } *user_size = n; }
    pthread_mutex_unlock(&mu);
    if (!r) { *w = tw; *h = th; }
    return r;
}
static uint32_t image_plain(int *w, int *h, void *image, size_t size, void *work, size_t work_size, const Entry *e) { (void)work; (void)work_size; return load(w, h, NULL, NULL, 0, image, size, e, false); }
static uint32_t image_user(int *w, int *h, uint64_t *us, void *u, size_t uc, void *image, size_t size, void *work, size_t work_size, const Entry *e) { (void)work; (void)work_size; return load(w, h, us, u, uc, image, size, e, false); }
static uint32_t thumb_user(int *w, int *h, uint64_t *us, void *u, size_t uc, void *image, size_t size, void *work, size_t work_size, const Entry *e) { (void)work; (void)work_size; return load(w, h, us, u, uc, image, size, e, true); }
/* AlbumFileAttribute: 16 bytes, a flag and zero (sdk+0x5032c); a snapshot has neither. */
static uint32_t thumb_attr(int *w, int *h, uint8_t *attr, void *image, size_t size, void *work, size_t work_size, const Entry *e) {
    (void)work; (void)work_size; if (attr) memset(attr, 0, 16); return load(w, h, NULL, NULL, 0, image, size, e, true);
}
static uint32_t thumb_attr_user(int *w, int *h, uint8_t *attr, uint64_t *us, void *u, size_t uc, void *image, size_t size, void *work, size_t work_size, const Entry *e) {
    (void)work; (void)work_size; if (attr) memset(attr, 0, 16); return load(w, h, us, u, uc, image, size, e, true);
}
/* nn::time::CalendarTime packed in x0 the way the SDK returns it (sdk+0x509c8). */
static uint64_t created_time(const Entry *e) {
    if (!e) runtime_fail("album entry null");
    return (uint64_t)e->year | (uint64_t)e->month << 16 | (uint64_t)e->day << 24 | (uint64_t)e->hour << 32 | (uint64_t)e->minute << 40 | (uint64_t)e->second << 48;
}

static uint32_t album_error(void) { return R_ALBUM; }
static uint32_t ok(void) { return 0; }
static bool no(void) { return false; }
static uint64_t work_size(void) { return 0x1000; }
static uint32_t native_window(void **out) { if (out) *out = NULL; return R_ALBUM; }
static uint32_t precheck(uint64_t size) { (void)size; fprintf(stderr, "[album] replay to video: not made on the host (album error reported to the game)\n"); return R_ALBUM; }

void *runtime_album_override(const char *name) {
    static const struct { const char *name; void *fn; } entries[] = {
        {"_ZN2nn5album10InitializeEv", initialize}, {"_ZN2nn5album8FinalizeEv", initialize},
        {"_ZN2nn5album26GetAlbumScreenshotFileListEPiPNS0_14AlbumFileEntryEi", screenshots},
        {"_ZN2nn5album21GetExtraMovieFileListEPiPNS0_14AlbumFileEntryEi", no_movies},
        {"_ZN2nn5album14SaveScreenshotEPKvmNS0_9ImageSizeENS0_17AlbumReportOptionE", save_plain},
        {"_ZN2nn5album26SaveScreenshotWithUserDataEPKvmNS0_9ImageSizeENS0_16ImageOrientationENS0_17AlbumReportOptionES2_m", save_user},
        {"_ZN2nn5album22SaveAndShareScreenshotEPKvmNS0_9ImageSizeENS0_17AlbumReportOptionERKNS_7account3UidEPKc", save_share},
        {"_ZN2nn5album14LoadAlbumImageEPiS1_PvmS2_mRKNS0_14AlbumFileEntryE", image_plain},
        {"_ZN2nn5album14LoadAlbumImageEPiS1_PmPvmS3_mS3_mRKNS0_14AlbumFileEntryE", image_user},
        {"_ZN2nn5album23LoadAlbumThumbnailImageEPiS1_PmPvmS3_mS3_mRKNS0_14AlbumFileEntryE", thumb_user},
        {"_ZN2nn5album23LoadAlbumThumbnailImageEPiS1_PNS0_18AlbumFileAttributeEPvmS4_mRKNS0_14AlbumFileEntryE", thumb_attr},
        {"_ZN2nn5album23LoadAlbumThumbnailImageEPiS1_PNS0_18AlbumFileAttributeEPmPvmS5_mS5_mRKNS0_14AlbumFileEntryE", thumb_attr_user},
        {"_ZN2nn5album23GetAlbumFileCreatedTimeERKNS0_14AlbumFileEntryE", created_time},
        {"_ZN2nn5album20DeleteExtraMovieFileERKNS0_14AlbumFileEntryE", album_error},
        {"_ZN2nn5album20OpenAlbumMovieStreamEPNS0_17MovieStreamHandleERKNS0_14AlbumFileEntryE", album_error},
        {"_ZN2nn5album20ReadAlbumMovieStreamEPmPvmNS0_17MovieStreamHandleEl", album_error},
        {"_ZN2nn5album23GetAlbumMovieStreamSizeEPmNS0_17MovieStreamHandleE", album_error},
        {"_ZN2nn5album21CloseAlbumMovieStreamENS0_17MovieStreamHandleE", ok},
        {"_ZN2nn5album35GetMovieMakerRequiredWorkMemorySizeEv", work_size}, {"_ZN2nn5album40GetMovieMakerRequiredWorkMemoryAlignmentEv", work_size},
        {"_ZN2nn5album20InitializeMovieMakerEPvm", ok}, {"_ZN2nn5album18FinalizeMovieMakerEv", ok},
        {"_ZN2nn5album25PrecheckToStartMovieMakerEm", precheck}, {"_ZN2nn5album15StartMovieMakerERKNS0_24MovieMakerMovieParameterE", album_error},
        {"_ZN2nn5album19IsMovieMakerRunningEv", no}, {"_ZN2nn5album20CheckMovieMakerErrorEv", ok}, {"_ZN2nn5album15AbortMovieMakerEv", ok},
        {"_ZN2nn5album16FinishMovieMakerEPKvm", album_error}, {"_ZN2nn5album16FinishMovieMakerEPKvmS2_mNS0_9ImageSizeE", album_error},
        {"_ZN2nn5album27EncodeMovieMakerAudioSampleEPKvm", album_error}, {"_ZN2nn5album25GetMovieMakerNativeWindowEPPv", native_window},
    };
    for (size_t i = 0; i < sizeof entries / sizeof *entries; i++) if (!strcmp(name, entries[i].name)) return entries[i].fn;
    return NULL;
}
