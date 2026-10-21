# BOTW port: development notes and handoff

State on 2026-10-21. Run the tests with `.venv/bin/python -m unittest discover -s tests`. The measured performance
work is summarized in [PERFORMANCE.md](PERFORMANCE.md); the player-facing notes are in [README.md](README.md).

## Done and verified

| Topic | What changed | How it was verified |
|---|---|---|
| Bounded app shader build | `prepare_botw_shaders.py` has a `startup` planner (app, ~26,000 variants) and `coverage --all-variants` (`build_shaders.py`, ~147,000); the seed identity includes the planner; the translator removes its intermediates per request | Real builds; `test_botw_shaders`, `test_shader_resume`, `test_build_cli` |
| Virtual-core preemption | `runtime/threads.c`: `[core-stall]` diagnostics and Horizon-style preemption (`core_force`, BOTW only) | In-game A/B: frames > 33 ms 7.3 % → 2.7 %; `test_core_preemption` with a negative control and a stress case (6 pinned threads, mixed priorities, spinning without SVCs) |
| Job-worker timeslice, on by default | `core_timeslice_ns` (2 ms, `BOTW_CORE_TIMESLICE_MS=0` off), `core_polling` (no SVC during a whole timeslice, sampled by the waiters), `game_patches.c:runtime_game_spin_lendable` (only in the queue loop `main+0xef6354..0xef637c` / `0xef63e8..0xef6410`, outside the spinlock taken by the `stxr` at `0xef6378`/`0xef640c` and released by the `stlxr` at `0xef63e0`, never participant 0) | Scripted A/B with seeded input: p99 64–80 → ~24 ms, over33 ~3.5 % → ~0.1 %, no frame > 100 ms; probe case "one SVC, then polling" fails with the old rule and passes with the new one |
| NVN translation thread, on by default | `hle_nvn.c:async_replay` (BOTW default on, `TOTK_ASYNC_REPLAY=0` off); `COPY_TOKEN_ASYNC` keeps recording-copy marks in that mode and `copy_token_releasable` frees a copy only once its last submission (`used_at`) was replayed (`g_replay_at`, from `xjob.at`) | Same-seed A/B: p90 ~20 → 16.8 ms, p99 ~24 → ~19 ms; an 18-minute session: no dead handle, live copies flattening near 63,000 (without marks: 40,000 → 69,000 in 13 minutes and rising) |
| No closing on a dead command handle (BOTW) | `copy_touch` returns 0; `nvnQueueSubmitCommands` drops that handle and `inline_recording` skips it; copies freed by their mark go to their own ring (`g_freed_token`) so the report names the mechanism | A real load into the world ended once in ~20 sessions with "command handle … is not a live recording"; compiles for BOTW and TOTK; build self-tests pass |
| Optional DLC | `--dlc` ×2; `scripts/extract_aoc_romfs.py`; `games/botw/build.py:extract_dlc`; `romfs.c` (`aoc` mount), `services.c` (index list), `fs_host.c` (unmount by name), `applet_host.c` (eShop and version-error applets return) | `test_botw_dlc`; `probe-fs` with the DLC and a negative control; in game: *Downloaded sets* screen, *Master Mode* and *DLC Ver.3.0* in the menu |
| Spotlight kept out | `game_storage.h:nsw_shader_cache_path` (`Shaders Cache/rt-<chain>.noindex`, renamed by the launcher), `build_app.work_tree` (`<work>/<game>.noindex`, renamed under `lock_work`) | `test_game_storage`, `test_work_directory`; a `*.noindex` folder was not indexed on macOS 26 while a plain one and one with `.metadata_never_index` were |
| Faster builds | One `cp -cRL` clone for packaging, hard-linked shader libraries, `codesign --verify --ignore-resources`, a verification record written during the build so the first start does not re-hash 15 GB | `test_copy_tree`, `test_build_cli`; packaging 6 min 37 s → 3 min 29 s |
| Seed install and resume | `seed-installed.json` remembers up to 8 installed seeds; existence checks with 16 parallel `stat`s; resumed files are hard-linked straight to their final names | `test_shader_seed`, `test_shader_resume`; cache check at startup 9.4 s → 0.55 s |
| One build per work folder | `build_app.lock_work` (`<work>/.build.lock`) | `test_build_cli` |
| Session tool and quick start | `games/botw/session.py run/compare/stalls`; `nsw_quickstart.py` | `test_botw_session`, `test_quickstart` |

## Discoveries that need no new investigation

- **DLC:** pack 1 (`…F001`) carries the data of both packs; pack 2 (`…F002`) only `System/AocVersion.txt`. BOTW enables
  its DLC only when `ListAddOnContent(out,0,2)` returns 2 contents including index 1 (`main+0x10cc2f8`).
- **1.0.0 is not an option of this port:** it uses SDK 0.12.14 with NVN and four other SDK modules separate; 1.9.0 uses
  SDK 20.5.22.
- **The job queue:** the loop at `main+0xef6354` is a variant of `sead::FixedSizeJQ::run`. `w0 = TLS − 1`
  (`main+0xef631c`; `main+0x1573a9c` stores `GetCurrentCoreNumber() + 1` in the slot `*(u32 *)*(main+0x1d66320)`; the
  SDK's `GetTlsValue` reads `*(*(TLR+0x1f8) + 0x80 + slot*8)`). Only participant 0 (the main thread `0x1001`) may take
  the first batch; the others spin until `taken > 0`. `[core-stall]` prints the participant index of spinners and of
  waiting threads.
- **What does not cause the drops:** the GPU, shader compilation and presentation. `TOTK_FREE_CORES=1` closes BOTW.
- **Measuring:** Spotlight indexing after a build and a sleeping display both invalidate frame-time comparisons.
- **`codesign` on a 16.5 GB bundle** takes ~108 s (SHA-1 and SHA-256 of every resource at ~150 MB/s);
  `--digest-algorithm=sha256` does not change it. A build log's modification time is its last write: a step that
  prints nothing shows when it started.

## Not reached

- **TOTK** was not built or played with these changes (no TOTK files were available). The shared changes compile in
  the TOTK configuration; BOTW-only behaviour is behind `#ifdef NSW_GAME_BOTW`, but `[core-stall]`, the SVC count and
  the empty DLC list also run in TOTK.
- **Upstream sync:** this fork starts from MacNativeGames `b75be8c`. Upstream has since added about 180 commits,
  including its own BOTW port; merging them means reconciling two ports on the same runtime.
- Unmeasured: `TOTK_YIELD_BACKOFF` and the remaining items in [PERFORMANCE.md](PERFORMANCE.md).
- **Root cause of the dead command handles:** in 2 of 41 sessions, 3 command handles were submitted again ~17 times each
  during the 15-second load into the world, after their copies were gone; none was in either freed-handle ring, so they
  were released long before (the re-add ring wraps in ~30 s during a load) or never recorded by us. They are now skipped
  (at worst part of the loading screen is not drawn for those frames); before, the game closed.

## Pending (needs play)

1. Play DLC content (for example *The Master Trials* items or *The Champions' Ballad* quest) and press "Nintendo eShop"
   in the DLC menu: it should return to the menu without closing the game.
2. A long session (an hour or more) with the defaults, to confirm the recording-copy count stays bounded with the
   translation thread.

## Exact next step

Play an hour with the default build, then read `session.py stalls <name>` and the last `recording copies` line of
`game.log`. If the copies keep growing, compare with `TOTK_ASYNC_REPLAY=0` before changing anything else.
