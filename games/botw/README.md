# Breath of the Wild on macOS

Experimental native adapter for Apple silicon and macOS 26 or later. It runs the rewritten ARM64 modules with macOS
services and Metal graphics. The builder supports base 1.0.0 with update 1.9.0; other executables are rejected by hash.
The DLC is optional (see [DLC](#dlc)).

From the project root:

```sh
python3.14 build_app.py --game botw /path/to/base.nsp \
  --update /path/to/update-1.9.0.nsp -k /path/to/prod.keys \
  --firmware /path/to/firmware --work /path/to/BOTW_build \
  -o '/path/to/Breath of the Wild.app'
```

## DLC

Add both add-on content packs with `--dlc` (in any order), for the app build and also for `build_shaders.py`:

```sh
  --dlc '/path/to/DLC Pack 1 [01007EF00011F001].nsp' --dlc '/path/to/DLC Pack 2 [01007EF00011F002].nsp'
```

Both packs are required, or none:

- **Pack 1 (`…F001`)** carries the data of both packs (1.39 GB).
- **Pack 2 (`…F002`)** holds only `System/AocVersion.txt`; it is the licence for *The Champions' Ballad*.
- **Why one pack alone does not work:** BOTW 1.9.0 calls `ListAddOnContent(out, 0, 2)` and enables its DLC only when it
  gets exactly two contents, one of them index 1 (`main+0x10cc2f8`). It then mounts index 1 as `aoc` and requires
  version 3.x.

The DLC adds no shader programs of its own (17,683 shared with the game), so the shader cache also covers its areas.

The builder rejects a single pack before it starts. It verifies all six IVFC levels of both packs and compares their
hashes with `assets/expected-v1.9.0.json` (add-on content 3.0). The runtime serves `aoc:` from
`Contents/Resources/botw/extracted/aoc` (`runtime/romfs.c`) and lists indices 1 and 2 (`runtime/services.c`). A save
made with the DLC may depend on it: built without the DLC later, the game behaves as on a console without it.

## Why update 1.9.0 is required

Base 1.0.0 cannot be an option of the same port:

- **Different SDK:** it uses NintendoSDK 0.12.14 (2017); 1.9.0 uses 20.5.22.
- **Different modules:** the 1.0.0 `main` depends on five separate SDK modules (`nnSdk`, `nvn`, `nnDisplay`, `libcurl`,
  `multimedia`).
- **What would have to be redone:** the runtime replaces NVN, `nn::fs`, services and IPC by symbol name and by the
  structures of the SDK 20.x generation, and the NVN-to-Metal translation follows that generation's API. Supporting
  1.0.0 is a new port: rewrite and load those modules, cover NVN 0.12's HLE functions and structures, locate and verify
  the VFR patch again, and validate everything in play.
- **Incompatible saves:** a 1.0.0 game cannot read 1.9.0 saves; it would need its own data folder.

The builder extracts and verifies the original files, rewrites the modules, prepares shaders, compiles the runtime and
the launcher, runs audio and loader checks, and packages and signs the app locally. It never copies an executable from
earlier development tests: every runtime change goes through these sources and this builder. The app carries a manifest
of its sources and binary in `Contents/Resources/build-manifest.json`.

The original files are never modified. The source distribution includes no games, keys, firmware, firmware fonts,
derived shaders or apps containing the game. See the project [LICENSE](../../LICENSE); third-party notices are kept.
The licence grants no rights to the game.

## Portable data

The app and the shader installer prepare:

```text
~/Documents/NSW Games/Breath of the Wild/
  Saves/
  Shaders Cache/
  Screenshots/
  Settings/
  Mods/
  Logs/
  Cache/
  game.json
```

To move to another Mac, close the game and copy the whole folder together with your app. `Settings` also keeps the
account identifier that selects the saves in `Saves`. Data from the old standard Library folder is copied when the new
folder is prepared; the originals are kept. F12 saves a screenshot to `Screenshots`. `NSW_GAMES_DIR` selects another
root folder.

## Shaders without opening the game

```sh
python3.14 build_shaders.py --game botw /path/to/base.nsp \
  --update /path/to/update-1.9.0.nsp -k /path/to/prod.keys \
  --work /path/to/BOTW_build
```

It needs Apple's Metal toolchain:

```sh
xcodebuild -downloadComponent MetalToolchain
```

It reuses the verified intermediate data, extracts shaders from packages, effects, interfaces and modules, translates
them and compiles them to Metal libraries with safe math. Unlike the app build, which prepares only the default binding
state and the ones already seen in play (about 26,000 variants, so packaging finishes), this script adds every texture,
attribute and output type profile: about 147,000 variants. If it is interrupted, the next run continues from the
SHA-256-verified receipts; a half-written translation or library is never published.

Disk space (measured with both DLC packs): the seeds and libraries take about 8 GB in the work folder and the installed
cache (`Shaders Cache`) 6.3 GB. The whole work folder is about 24 GB, including the 15 GB RomFS. The two modes are kept
apart (`build/shader-seed/v0` for the app, `v0-all` for this script), so an app build does not force a new translation.

Measured on an M4: the first complete run takes about 36 minutes (translating and compiling everything). Later runs
take 7–8 minutes when something changed (for example, new binding states seen in play make it re-verify the 147,000
files) and 3.5–6 minutes when nothing changed (verification and installation only). It installs the result in
`Shaders Cache/rt-botw-native-v1-control-size.noindex` and keeps variants collected earlier. The game must be closed
during installation. This script does not start a game or run a recorded route.

The runtime and the extractor share the same shader identity: the old controls exclude the reflection tails that can
hold relocated pointers, so the preload can be reused between sessions. Binding states and pipeline combinations known
only at run time can still cause extra work; removing every stutter is not guaranteed. The manifest reports skipped
translations and compiled libraries.

## Fixes built into the builder

- Real time with BOTW 1.9.0's variable frame rate, keeping its multipliers and per-core buffers. Verified at 15–120 Hz
  and variable rates; below 15 FPS the game can slow down. Long pauses do not turn into physics jumps.
- Continuity between PCM/ADPCM buffers with EndOfStream: the marker resets the counter without inserting silence when
  another buffer is queued. The audio probe passes 219,403 checks; the audible gaps every ~300 ms are gone.
- NVN depth convention, inactive duplicate colour attachments and zero-sized dispatches adapted to Metal.
- Saves with CommitSaveData, the BOTW identity and portable folders.
- Gyroscope drift configuration handled without opening Switch HID; calibration comes from the host's motion fusion.
- Virtual-core preemption and the job-worker timeslice, translation on its own thread, and the other performance
  changes described in [PERFORMANCE.md](PERFORMANCE.md).

## Development checks

The tests stay in the checkout and are excluded from the source package:

```sh
.venv/bin/python -m unittest discover -s tests
.venv/bin/python games/botw/verify_timing.py /path/to/main.patched.bin
```

A complete playthrough, all physics, DLC content in play and long-session stability have not been certified. TOTK
keeps its own adapter and cache namespace; a local BOTW test does not validate a real TOTK build on its own.
