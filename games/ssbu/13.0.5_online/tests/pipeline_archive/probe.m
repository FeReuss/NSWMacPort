/* Metal pipeline archive writes (totk_pipeline_archive.h) across processes: a write waits while another instance holds
   the gpuarchiver lock, so its cleanup cannot remove the build folder that instance's Metal is still using. No game. */
#import <Foundation/Foundation.h>
#include <assert.h>
#include <spawn.h>
#include <stdio.h>
#define MLOG(...) fprintf(stderr, __VA_ARGS__)
static NSString *g_tmp;
NSString *totk_rt_dir(void) { return g_tmp; }
#include "../../third_party/d2r_gfx/gfx/nvn/totk_pipeline_archive.h"
static int par_game_idle(void) { return 1; }
extern char **environ;

int main(int argc, char **argv) { @autoreleasepool {
    if (argc == 2 && !strcmp(argv[1], "hold")) {   // the other instance: hold the lock 0.8 s
        int fd = par_ga_lock(); assert(fd >= 0); fputs("held\n", stdout); fflush(stdout);
        usleep(800000); par_ga_unlock(fd); return 0;
    }
    char dir[] = "/tmp/pipeline-archive-probe-XXXXXX"; assert(mkdtemp(dir)); g_tmp = @(dir);
    [NSFileManager.defaultManager createDirectoryAtPath:par_dir() withIntermediateDirectories:YES attributes:nil error:nil];
    id<MTLDevice> dev = MTLCreateSystemDefaultDevice(); assert(dev);
    id<MTLLibrary> lib = [dev newLibraryWithSource:@"kernel void k(device float *a [[buffer(0)]]) { a[0] = 1.0; }" options:nil error:nil];
    MTLComputePipelineDescriptor *d = [MTLComputePipelineDescriptor new]; d.computeFunction = [lib newFunctionWithName:@"k"];
    assert(d.computeFunction);
    int out[2]; assert(!pipe(out));
    posix_spawn_file_actions_t fa; posix_spawn_file_actions_init(&fa); posix_spawn_file_actions_adddup2(&fa, out[1], 1);
    pid_t child; char *args[] = {argv[0], "hold", NULL};
    assert(!posix_spawn(&child, argv[0], &fa, NULL, args, environ)); close(out[1]);
    char line[8] = {0}; assert(read(out[0], line, 5) == 5 && !strcmp(line, "held\n"));
    uint64_t t0 = clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
    par_write(dev, @"probe", d);
    double waited = (clock_gettime_nsec_np(CLOCK_UPTIME_RAW) - t0) / 1e9;
    int status; waitpid(child, &status, 0); assert(WIFEXITED(status) && !WEXITSTATUS(status));
    assert(waited > 0.5);   // the write waited for the other instance
    assert([NSFileManager.defaultManager fileExistsAtPath:par_file(@"probe")]);
    printf("pass: the archive write waited %.2f s for another instance's lock\n", waited);
    return 0;
} }
