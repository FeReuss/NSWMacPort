# BOTW performance on macOS: status and next steps

Measurements from 2026-10-21 on an M4 MacBook Air (16 GB, 60 Hz display), BOTW 1.9.0 with both DLC packs.

## How to measure

1. Launch with per-frame logging:
   `open --env DM_NVN_TSV=<Logs>/frames.tsv --env DM_NVN_TSV_CALLS=0 "Breath of the Wild.app"`
   (`games/botw/session.py run <name>` does this, archives the logs and compares sessions).
2. Copy `Logs/game.log` before opening the game again: the launcher rotates it.
3. Compare sessions in the same area with the same activity. Count world frames only (`@t_s > 60`, `@draws > 1000`) and
   report FPS, p50/p90/p99 and the share of frames over 33/50/100 ms.
4. Attribution:
   - `[长帧]` lines in `game.log` split pipeline/library compilation, texture uploads, GPU and submission.
   - `@cpu_ms` and `@proc_cpu_ms` in the TSV split the presenting thread from the whole process.
   - `[core-stall]`, `@core_wait_ms`, `@core_wait_max_ms` and `@core_preempted` measure the virtual cores;
     `session.py stalls <name>` summarizes who waited, who held the core and the job-queue participants.
5. Try risky changes on copies of the saves (`session.py run <name> --save-copy`).
6. Before measuring: keep the display awake (`caffeinate -d`; without display-link callbacks the runtime marks the
   presentation data invalid) and check that Spotlight is idle (`top -o cpu`: no `mds_stores` or `fseventsd` at the
   top). Indexing after a build kept all efficiency cores busy for hours and tripled the 99th percentile.

Unattended A/B runs drive the game through `TOTK_INPUT_FILE` (`key`, `down`/`up`, `shot`, `close`) with
`TOTK_ASSUME_FOCUS=1` and `TOTK_SLOT_OWNER=agent`, on save copies, with the same seeded random input for both variants
of a pair. If `[input] user input` appears in `game.log`, someone used the window: the run is void and must not be
closed by the script.

## Done

| Change | Evidence | Result |
|---|---|---|
| Horizon-style preemption of virtual cores (`core_force`, `runtime/threads.c`, BOTW only) | Job workers (prio 44) poll at `main+0xef6354` without SVCs; threads of prio 30–32 pinned to the same core waited 236 s per 400 s | Frames > 33 ms 7.3 % → 2.7 %; p90 29.3 → 21.1 ms |
| Timeslice on by default (2 ms; `BOTW_CORE_TIMESLICE_MS=0` turns it off), limited to a worker polling its queue without the lock (`game_patches.c:runtime_game_spin_lendable`), never participant 0 | Participant 0 of `sead::FixedSizeJQ` is the main thread `0x1001`; while it translates NVN to Metal, workers `0x1008`/`0x1009` spin on cores 1–2 and threads of prio 45–49 pinned there starve | Three same-seed pairs: p99 64–80 → 31.8–32.3 ms, over33 2.9–3.8 % → 0.9 %, over100 0.3–0.6 % → 0 |
| "No SVC during a whole timeslice" sampled by the waiters (`core_polling`) instead of "no SVC since taking the core" | 149 of ~210 remaining stall owners were workers back from a job | p99 31.8–32.3 → 23.9–24.3 ms, over33 0.9 → 0.0–0.2 %, longest core wait p99 24–26 → 11 ms |
| NVN translation on its own thread by default (`TOTK_ASYNC_REPLAY=0` turns it off), with recording-copy marks kept in that mode (`COPY_TOKEN_ASYNC`) | Without marks, live copies grew 40,000 → 69,000 in 13 minutes | p90 19.9–21.7 → 16.8 ms, p99 ~24 → 18.6–19.7 ms; 18 minutes: no dead handle, copies flattening near 63,000 |
| A dead command handle is logged and skipped (BOTW) instead of closing the game | One load into the world in ~20 sessions ended with "command handle … is not a live recording" | No closing; the freed-handle report names the mechanism that released it |
| Bounded app shader build; complete, resumable `build_shaders.py` | 17,683 shaders expanded to 146,665 variants | The build finishes; the complete cache is installed separately |
| Shader cache and build folder named `*.noindex` | Spotlight indexed 147,014 libraries of the cache and ~67,000 in the build folder | Verified: a `*.noindex` folder is not indexed (a `.metadata_never_index` marker is) |

Ruled out with data: the GPU (~7 ms per frame), shader compilation (15–33 of thousands of long frames) and the render
thread (~4 ms). `TOTK_FREE_CORES=1` makes BOTW close at startup: the game needs the virtual cores. `BOTW_CORE_SLICE_US=100`
(forcing before the first 1 ms wait) brought nothing on top of the timeslice and raised preemptions from 46 to 69 per
second.

## Remaining possibilities, highest expected value first

1. **Spinners with `sched_yield`.** In samples, ~0.25 cores go to `swtch_pri` (polling with `nn::os::YieldThread` →
   `svcSleepThread(-1)`). `TOTK_YIELD_BACKOFF=on` exists; in TOTK it increased dropped frames, in BOTW it is unmeasured.
2. **Gate dispatch (low priority).** `runtime_dispatch` (`runtime/loader.c`) searches the ~193 gates linearly on every
   SVC and every `cntpct_el0` read; it does not show among the hot functions of `sample`. Measure `timer_calls` and
   `svc_calls` per second before changing it.
3. **First session without pipeline stalls.** BOTW does not prewarm the offline seed (`warm_full_cache()` returns NO so
   ~147,000 variants are not kept in memory); it prewarms only `pso_user.tsv`. A first session had 33 frames creating
   pipelines (8 s in total). Options: prewarm the pipeline rows of the bounded seed, or use `MTLBinaryArchive`.
4. **Presentation pacing.** The display runs at 60 Hz and the VFR accepts 15–120 Hz. An optional 30 or 60 cap (`DM_FPS`
   exists as a test switch in `nvn_mtl.m`) gives steadier motion when the average oscillates; a player option, not a fix.
5. **Thermals.** The MacBook Air has no fan. Measure 30-minute sessions or longer before blaming late drops on the game.

## Tried and discarded in the shader pipeline

- **Stat-based receipts** (skip re-hashing files with unchanged size, time and inode): resuming the complete seed only
  went from 5 min 47 s to 5 min 32 s. The cost is metadata (`rename`/`stat`/`link`/`open`) in a directory of ~300,000
  entries, not hashing. Updating the staging in place or splitting it into subfolders would help, but both change the
  seed and runtime cache formats.
- **Game data in a nested bundle signed once** (to skip resealing 16.5 GB): signing went only from 108.8 s to ~70 s,
  because `codesign` revalidates nested code; not worth changing the bundle layout.
