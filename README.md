# NSWMacPort

> **Pure AI slop what just works**

Native macOS builds of *The Legend of Zelda: Breath of the Wild* and *Tears of the Kingdom*, made from **your own** game
files. No emulator: the game's ARM64 code is rewritten to run directly on Apple silicon, Switch system services are
answered by a native host, and NVN graphics are translated to Metal.

NSWMacPort is a fork of **[MacNativeGames](https://github.com/ly05010419/MacNativeGames)** by Yong Li and its
contributors. Thank you to the MacNativeGames authors: the rewriter, the Horizon host, the NVN-on-Metal layer, the
Tears of the Kingdom port and the build tooling all come from that project. This fork starts from MacNativeGames commit
[`b75be8c`](https://github.com/ly05010419/MacNativeGames/commit/b75be8c) (October 20, 2026) and does not include the
upstream changes made after it (upstream has since added its own Breath of the Wild port).

## Legal

**You must own the game.** Use this project with your own purchased copy of the game, and keys and system files dumped
from your own Nintendo Switch. This is an independent project, unaffiliated with Nintendo. The Legend of Zelda and
Nintendo Switch are trademarks of Nintendo. The source-code license grants no rights to Nintendo's game content,
firmware or keys.

This repository contains port code and build tools only. It contains **no** keys, game files, updates, DLC, firmware,
extracted or rewritten game code, or shaders made from the game: game containers, RomFS, firmware fonts, translated
game shaders, keys and saves stay on your computer. The generated app contains game material; do not distribute it.
Compatibility metadata includes addresses, hashes, shader identifiers and small instruction encodings used by patch
guards and replacements; these are not a license to redistribute any game file.

## What this fork adds

- **Breath of the Wild 1.9.0 on macOS** (`--game botw`): build adapter (`games/botw/build.py`), real-time variable
  frame rate, audio end-of-stream fix, NVN depth-range fix, stable shader identity and a bounded app shader build.
- **Optional BOTW DLC:** both add-on content packs (`--dlc` twice) are verified and mounted; the game shows *Master
  Mode* and the downloaded sets. One pack alone is rejected with a clear message (the game enables its DLC only with
  both).
- **Smoother frame rate for BOTW** (measured with scripted A/B sessions on an M4, same seeds, 5 minutes each):
  - Horizon-style preemption of virtual cores, plus a 2 ms timeslice that lends the core of a job worker polling its
    queue without an SVC (never while it holds the queue lock, never the main thread). 99th-percentile frame time
    64–80 ms → about 24 ms; frames over 33 ms ~3.5 % → ~0.1 %; no frames over 100 ms.
  - The NVN-to-Metal translation runs on its own thread by default, with recording-copy marks kept bounded: 90th
    percentile 20–22 ms → 16.8 ms, 99th percentile ~24 → ~19 ms (`TOTK_ASYNC_REPLAY=0` turns it off,
    `BOTW_CORE_TIMESLICE_MS=0` turns the timeslice off).
- **Stability:** a command list that is no longer a live recording is logged and skipped instead of closing the game
  (BOTW); Spotlight no longer indexes the ~150,000-file shader cache or the build folder (`*.noindex`), which used to
  keep the efficiency cores and the disk busy for hours after every build and slowed the game.
- **Complete offline shader preparation:** `build_shaders.py` covers every type profile (about 147,000 variants for
  BOTW), resumes after an interruption from hash-checked receipts and never publishes a half-written shader; an app
  build stays bounded (about 26,000 variants).
- **Faster builds:** packaging with one APFS clone, hard-linked shader libraries, verification records that avoid
  re-hashing 15 GB on the first start.
- **Quick start:** `nsw_quickstart.py` installs the dependencies and asks for your files (see below).
- Session tools for measuring performance (`games/botw/session.py`) and development notes
  (`games/botw/HANDOFF.md`, `games/botw/PERFORMANCE.md`).

## Quick start

On a Mac with Apple silicon and macOS 26 or later, clone the repository and run:

```sh
git clone https://github.com/FeReuss/NSWMacPort.git
cd NSWMacPort
python3 nsw_quickstart.py
```

The script checks the Xcode command line tools and Homebrew, installs `opus`, `libvpx` and `python@3.14`, then asks
which game to build and where your files are: you can drag a file or folder onto the Terminal window, or give one
folder and let it find the base game, update, DLC, `prod.keys` and firmware in it. It prints the build command it
will run, builds the app, and can then prepare the complete shader cache.

## Shaders

No precompiled shaders are included: they are translations of the game's own shaders, so they are made on your Mac
from your game files. There are two ways to get them:

- **Building the app** prepares the startup set (about 26,000 variants for BOTW) and installs it in the cache.
- **`build_shaders.py`** (or answering *yes* at the end of the quick start) prepares every variant (about 147,000 for
  BOTW: about 35 minutes on an M4 and ~15 GB of extra disk space the first time, a few minutes for later runs). It can run while you are not playing,
  and later runs resume where they stopped.

The cache lives in `~/Documents/NSW Games/<game>/Shaders Cache/rt-<chain>.noindex/` (for example
`~/Documents/NSW Games/Breath of the Wild/Shaders Cache/rt-botw-native-v1-control-size.noindex/`). To reuse a cache
on another Mac with the same build, close the game, then copy that folder to the same place there:

```sh
mkdir -p ~/Documents/NSW\ Games/Breath\ of\ the\ Wild/Shaders\ Cache
cp -R /path/to/rt-botw-native-v1-control-size.noindex ~/Documents/NSW\ Games/Breath\ of\ the\ Wild/Shaders\ Cache/
```

The folder is named after the shader translation chain, so a build with another chain uses its own folder instead of
an incompatible one. Keep the cache private: it is made from the game.

## Choose the game

Both games use `build_app.py`; `--game` selects the adapter. See [tools and prerequisites](#what-you-need) below. Use your base 1.0.0 XCI/NSP, your keys, and the separate update when required:

```sh
python3.14 build_app.py --game totk ~/Games/totk.xci -k ~/keys/prod.keys --update ~/Games/totk-1.4.3.nsp
python3.14 build_app.py --game botw ~/Games/botw.nsp -k ~/keys/prod.keys --update ~/Games/botw-1.9.0.nsp --firmware ~/Games/firmware
# BOTW with its DLC: both add-on content packs, in any order
python3.14 build_app.py --game botw ~/Games/botw.nsp -k ~/keys/prod.keys --update ~/Games/botw-1.9.0.nsp --firmware ~/Games/firmware \
  --dlc ~/Games/botw-dlc-pack1.nsp --dlc ~/Games/botw-dlc-pack2.nsp
```

TOTK supports base 1.0.0 and updates 1.1.2, 1.2.1 and 1.4.3; omitting `--game` selects TOTK. BOTW requires its **1.9.0 update** (the base 1.0.0 uses a different NintendoSDK, so it would be a separate port) and is experimental: loading, the world, movement, menus, save reload and the DLC menus have been played, but full-game, physics, graphics and long-session correctness remain unverified.

Add `-o "/Applications/Breath of the Wild.app"` or `-o "/Applications/Tears of the Kingdom.app"` to select the output. Default work folders are `BOTW_build` and `TOTK_build` beside the base game; `--work` selects another folder. NSP app builds need your `--firmware` or `--shared-fonts` unless valid fonts are already cached. Keep original game inputs and keys private; the builder reads them without changing them.

## Prepare shaders without opening the game

`build_shaders.py` accepts the same game, update, keys and work-folder arguments. It inventories the original local game files and prepares shaders into that game's `Shaders Cache`; it does not build or launch the game app:

```sh
python3.14 build_shaders.py --game totk ~/Games/totk.xci -k ~/keys/prod.keys --update ~/Games/totk-1.4.3.nsp
python3.14 build_shaders.py --game botw ~/Games/botw.nsp -k ~/keys/prod.keys --update ~/Games/botw-1.9.0.nsp [--dlc … --dlc …]
```

For BOTW, an app build prepares only the default and locally observed binding state of each shader (about 25,500 variants), so packaging stays bounded; `build_shaders.py` adds every texture, vertex-attribute and output type profile (about 147,000 variants, about 35 minutes on an M4 the first time). An interrupted run resumes from its hash-checked receipts; a half-written translation or library is never published.

Close running native games before preparing their caches. Shader inventory and translation use your local originals; first-time dependency installation and pinned tool downloads still need internet access. Offline inventory cannot predict every runtime texture, vertex and render-state variant. New variants can still compile during Mac gameplay and cause stutter; this is not a complete shader-cache or stutter-free guarantee. Ahead-of-time GPU compilation requires Xcode's Metal toolchain; unavailable or failed compilations can remain for runtime.

## Shared TOTK shader list

> [!TIP]
> **The shared list helps prepare known TOTK shader variants before play.** New variants can still require compilation on Mac or be missing on iPhone/iPad. Player contributions improve coverage without including shader code.

The [shared shader list](docs/shader-list.md) now holds **26,832 shader variants** (23,867 shaders) from the Mac, iPhone and iPad. Every build makes them from the player's own game files, so the list holds identifiers only, never game data. Helping takes no setup:

1. **Play.** When your game meets a shader the list does not have yet, it notes it in `shader-list-contribution.txt`.
2. **On a Mac, every 500 new shaders it asks you once, before the game starts.** Click "Send": the GitHub form opens and the file is shown, ready to drag in. An iPhone or iPad never asks during play: while it is connected to the Mac that built its app (cable or the same Wi-Fi), the Mac asks instead. "Don't remind me again" stops the reminders.
3. **Everyone's next build can prepare them.** Matching variants can reuse this work.

**[Send your shaders →](https://github.com/ly05010419/MacNativeGames/issues/new?template=shader-list.yml)** · The game never uploads anything by itself.

Update history:

| Date | Platform | Update |
|---|---|---|
| Oct 19 | Mac | Key 4 switches to a true 21:9 ultrawide view and back (1.2.1, 1.4.3); the HUD keeps its shape in the middle 16:9 |
| Oct 19 | iPhone, iPad | Tested with 1.4.3: a fresh install shows the game from the first launch, without a played cache |
| Oct 19 | iPhone, iPad | Touch controls laid out like a Switch controller, with every button and both sticks |
| Oct 19 | All | The game notes the shaders the shared list lacks; on a Mac, every 500 it asks to send them in a [GitHub issue](https://github.com/ly05010419/MacNativeGames/issues/new?template=shader-list.yml) |
| Oct 19 | All | One shared [shader list](docs/shader-list.md); the build reads every shader in the game data (15,266 prepared for 1.4.3) |
| Oct 19 | Mac | 1.2.1 support (the most widely used update), with the same build command |
| Oct 19 | Mac | FPS, shader and load readings and the shortcut reminder in one small box in the window's top-right corner (`TOTK_HUD=corners` keeps the old layout) |
| Oct 18 | iPhone, iPad | IPA build target |
| Oct 18 | Mac | 1.4.3 support and shader prewarming: the title screen waits only for its own shaders (about 2 seconds), the rest warm in the background |
| Oct 17 | Mac | 1.0.0 and 1.1.2 support; no first-launch shader prewarming yet, so new scenes could stutter |

**Stuttering when you first start playing or enter a new scene is expected while new shaders compile—don't be surprised.** Performance typically improves as the cache warms up, and known variants from the [shared list](#shared-totk-shader-list) can reduce repeat compilation.

## Audio fix

Streamed music and dialogue lookup for `content://Sound/...` paths has been fixed. Opening dialogue and music were verified locally on version 1.0.0; other versions and scenes still need validation. The renderer also runs all known audio effect types and starts output after 20 ms of queued audio (2026-10-18). Rebuild your app with the original game inputs to apply the fix. See [audio troubleshooting and validation](docs/audio-troubleshooting.md) for details.

## Local testing

Tested on **MacBook Air M4 and Mac mini M4**. Local testing has collected **8,582 shaders**, with performance at approximately **90 FPS after shader warm-up, reaching 120 FPS in menus**.

Those are earlier local observations, not a stable frame-rate promise for every version or scene. Version 1.4.3 measurements and compilation stalls are recorded separately in [the test notes](docs/v1.4.3-port.md).

Shaders are automatically collected, compiled and cached as you play. Returning to previously visited scenes typically brings noticeable improvements in frame rates and smoothness because cached shaders can be reused. **8,582 is the number collected so far in local testing, not the game's total shader count or a collection limit**; the cache continues to grow as you explore.

**Shader prewarming on the first launch:** Building the app prepares shaders from your selected game data and includes them in the app: every shader in the game data for its default bind state, plus every variant named in the [shared shader list](docs/shader-list.md). Even with an empty user cache, the first launch imports these shaders. Only what the title screen itself uses is warmed before the first frame (about 2 seconds once macOS has compiled them before); the rest, together with compatible entries in the pipeline manifest, warms in the background behind the title, and the first trip into the world waits up to 15 seconds for it. No preliminary play session or manual cache copy is needed. The current 1.4.3 build prepares 15,266. Building the app also compiles them for your Mac's GPU. With Xcode's Metal toolchain installed they are compiled into the app (about 6 minutes on an M4 for 1.4.3) and work wherever the app goes. Without it they are compiled into macOS's Metal cache (a few minutes), and Metal finds them only while the game runs from where the app was built: if you move the app afterwards (to /Applications, for example), if macOS clears the cache (it does when the system disk fills up), or if another program was using Metal's cache while the app was built (a running game, for example; the build says so), the background warm-up compiles them again, which takes a few minutes, and new scenes can stutter until it finishes. To place the app elsewhere, build it there with `-o`; see [preparation and test details](docs/first-launch-shaders.md). Places no player has reported yet can still need new variants, which the Mac compiles while you play (a short stutter the first time). Playing adds them to your local cache, which is reused on later launches. The game also notes every variant the shared list does not have yet in `shader-list-contribution.txt`, and every 500 of them asks once whether to send the file in a [GitHub issue](https://github.com/ly05010419/MacNativeGames/issues/new?template=shader-list.yml) ("Send" opens the form and shows the file to drag in). It never uploads anything by itself. With git, you can also merge your cache and open a pull request, as [the list's guide](docs/shader-list.md) explains. The list holds shader and pipeline identifiers only, never game data, saves or compiled shaders; every build makes the shaders from the player's own game files.


## What you need

- A Mac with Apple silicon and **macOS 26** or later (the graphics use Metal 4); macOS 27 is supported. 16 GB of memory or more is recommended.
- About **25 GB** of free disk space for the work folder, in addition to your game file. Keep the output app on the **same APFS volume** to share the large data files through cloning. If cloning is unavailable or the app is on another volume, allow roughly **17 GB more** for the app copy.
- Your Tears of the Kingdom **base game 1.0.0** (`0100F2C0115B6000`) as an **XCI or NSP**, and your `prod.keys`. To build **1.1.2, 1.2.1 or 1.4.3**, supply its separate cumulative update as NSP or NSZ with `--update`; no intermediate update is required. NSZ is decompressed and its NCA hashes are verified before extraction. DLC and combined packages are unsupported.
- For NSP: its common ticket must be included unless the NCAs are rights-free. NSPs do not contain system shared fonts: supply an unpacked firmware NCA directory with `--firmware`, or previously extracted fonts with `--shared-fonts`. The six fonts are checked against this project’s reference dump; a valid BFTTF font from another firmware version is accepted and bound by its own bytes. Valid fonts already in the work folder are reused.

## Build the app

**1. Tools** (once): Xcode command line tools and [Homebrew](https://brew.sh). An internet connection is needed to install build dependencies and fetch pinned sources:

```sh
xcode-select --install
brew install opus libvpx python@3.14
```

For an iPhone/iPad build, install full **Xcode 26 or later** (including the iOS and Metal toolchains), then install the additional tools:

```sh
brew install xcodegen cmake ninja
```

Optional: with Xcode's Metal toolchain (`xcodebuild -downloadComponent MetalToolchain`), the build compiles the startup shaders into the app. It finds an Xcode kept outside `/Applications` too (on another volume, or set `DEVELOPER_DIR`). Without it, the build compiles them into the Mac's own Metal cache for the app's location instead, which does not move with the app: after moving it, the first launch compiles them again.

**2. Download or clone this repository**, then open Terminal in its `NSWMacPort` folder and run the script with your game file and keys:

```sh
python3.14 build_app.py --game totk ~/Games/totk.xci -k ~/keys/prod.keys
```

For an NSP, supply the shared fonts too:

```sh
python3.14 build_app.py --game totk ~/Games/totk.nsp -k ~/keys/prod.keys --firmware ~/Games/firmware
# Or reuse the six .bfttf files from a previous build:
python3.14 build_app.py --game totk ~/Games/totk.nsp -k ~/keys/prod.keys --shared-fonts /path/to/TOTK_build/totk.noindex/extracted/firmware/shared-fonts
```

`--firmware` accepts a directory of `.nca` files (unpack a firmware archive first), or an installed firmware `registered` directory. `--shared-fonts` accepts the extracted directory containing `0-standard.bfttf` through `5-nintendo-extension.bfttf`. No fonts or keys are downloaded. For an XCI, fonts are read from its system update partition by default; if that partition is missing, use one of these font options too. By default both input formats produce a macOS `.app`.

To build an unsigned iPhone/iPad IPA instead, add `--platform ios` and use an `.ipa` output path:

```sh
python3.14 build_app.py --game totk ~/Games/totk.xci -k ~/keys/prod.keys --platform ios -o ~/Games/TOTK.ipa
```

Add `--update ~/Games/1.4.3.nsp` to select an update, and keep the `--firmware` or `--shared-fonts` option when the base game is an NSP. The first iOS build downloads and compiles device versions of Opus and libvpx and compiles the shader seed for iOS, so it needs an internet connection and substantial temporary disk space (allow at least **45 GB free** for a fresh base build, or **60 GB** for a fresh build with an update). The resulting IPA is unsigned: sign and install it with your sideloading tool, keeping its entitlements (it uses the increased memory limit). iOS/iPadOS 26 and iPhone 14 or newer are required. See [iPhone and iPad](#iphone-and-ipad) for what has been tested.

Without `-o`, the selected output appears next to the game file (`Tears of the Kingdom.app` on Mac or `Tears of the Kingdom.ipa` for iOS). Before full shader preparation was added, a local clean base-1.0.0 Mac build took about 6 minutes on an M4 Mac mini (this is not a timing estimate for the current build; hardware, disk and network conditions affect the time): about a minute each to rewrite the main code module and to decrypt the 17 GB of game data, and two to three minutes for macOS to check the finished app once. The current build then also prepares the startup shaders (about 3 minutes for 1.4.3 on an M4) and compiles them for your GPU (a few more minutes; without the Metal toolchain this step is skipped, with a message, while a game is running). A Mac build needs no .NET or CMake, and the finished game needs no Python: its shader translator is a native program that comes with this repository (`games/totk/prebuilt`). Every intermediate file is in a work folder next to the game file (`TOTK_build`, or `--work`); running the script again reuses what is already done.

The extracted code modules, rewritten modules, RomFS and shared fonts are checked against the selected version's `games/totk/assets/expected-v1.0.0.json`, `expected-v1.1.2.json`, `expected-v1.2.1.json` or `expected-v1.4.3.json`; the prebuilt shader translator has its own SHA-256 check. A mismatch stops the build, with one exception: a different genuine retail dump of the same version is accepted — its `main.npdm` is checked by Nintendo’s ACID signature (`npdm_signed()`) instead of the pinned hash, and its firmware’s shared fonts are checked for a valid BFTTF structure and bound by their actual SHA-256. Key values are never printed.

Signing and verifying a large updated app can add several minutes. Keep the build running while it continues to report progress.

**3. Play:** open the app from Finder. The build starts the launcher in `--check` mode, which checks that the game executable exists and allows macOS to perform its first-launch checks; it does not run a gameplay test. You can keep the app where it was built or move it to `/Applications`; the first start after a move compiles the shaders again in the background (macOS's Metal cache does not follow the app to its new place), so to keep it in `/Applications`, build it there with `-o`. Copying it to a different volume can use additional disk space and trigger another macOS check, during which no window may appear. The first game launch verifies the game data (about 20 seconds in local testing). Newly encountered shaders can cause stutters; cached shaders reduce this work on later visits.


### Build version 1.1.2, 1.2.1 or 1.4.3

```sh
python3.14 build_app.py --game totk ~/Games/totk.xci -k ~/keys/prod.keys --update ~/Games/1.1.2.nsp
# NSZ updates are also accepted:
python3.14 build_app.py --game totk ~/Games/totk.xci -k ~/keys/prod.keys --update ~/Games/1.1.2.nsz
# Or build 1.2.1 or 1.4.3 directly from the same base:
python3.14 build_app.py --game totk ~/Games/totk.xci -k ~/keys/prod.keys --update ~/Games/1.2.1.nsp
python3.14 build_app.py --game totk ~/Games/totk.xci -k ~/keys/prod.keys --update ~/Games/1.4.3.nsz
# With a base NSP, keep --firmware or --shared-fonts as described above.
```

This produces the same **Tears of the Kingdom.app**, containing only the selected version. Quit the running game, then rebuild to the same `-o` path to replace the old app; there is no second game entry or version-selection menu. The window title shows the selected version, such as **Ver. 1.4.3**. Without `--update`, the script still builds 1.0.0.

You do not need to download separate 1.2.1 or 1.4.3 runtime tables or a shader cache. The build generates the version-specific runtime tables and first-launch shader seed from the game data you provide, then includes the shader seed in the app. The repository supplies the patch code and verified hashes of each supported update; only your base game, update and keys are external inputs.

Allow about **40 GB free for a fresh update build**, or **19 GB extra** when the base data is already cached, with the app on the same APFS volume. The merge reads the base NCA through a bounded range of the original container, temporarily extracts the much smaller update NCA, and verifies every IVFC level. It fetches a pinned [hactool](https://github.com/SciresM/hactool) source and builds it with clang/make. Temporary ticket files are private and removed after the merge; key values are not passed on the command line or printed.

Saves use the per-game `Saves` folder described below. Before the first launch of an updated TOTK version, the launcher backs up `Saves` and `Settings` together into `Backups/before-<version>`; a failed backup stops startup. Keep a separate copy before migrating older data or changing versions, and do not assume an updated save is backward compatible.

To keep one desktop entry, link the packaged app with the optional installer:

```sh
python3.14 games/totk/tools/install_desktop_entry.py --app "/path/to/Tears of the Kingdom.app"
```

The installer is a legacy desktop-link utility. The current launcher prepares the Documents layout described below; keep your older profile until you have checked the migrated saves in-game.

The source runtime passed basic Mac checks: load, movement/jumps, menus/map, 2K, save/reload and death retry. Full-map, long-session and fair 1.0/1.1.2 performance comparisons remain pending; the earlier FPS observations are not a promise of 1.1.2 performance. Packaging validation is recorded in [the build notes](docs/how-it-works.md#112-validation).

The 1.4.3 port has separately passed scene loading, movement/jumps, menus/map, save/reload, native 2K canvas switching and MetalFX upscaling/interpolation on an M4 Mac. The single packaged app has been tested, including save/reload, death/retry and shrine entry/exit. A three-hour earlier build and a 30-minute final-build run are recorded separately; first-use compilation stalls, long-session memory growth and audio underruns remain under investigation. See [the 1.4.3 test record](docs/v1.4.3-port.md) for the scope and follow-ups; this is not full-game or stable-FPS certification.

The 1.2.1 port (October 19) passed the same rewrite verification (600 of 600 negative controls) and the loader, RomFS and startup probes. On an M4 Mac it loaded saves in the 1.0.0 and 1.1.2 formats and passed movement/jumps, menus/map/quick menus, manual save and reload in a new process, fall death and retry, and the three key-3 picture modes; the packaged app was also opened through its launcher. See [the 1.2.1 test record](docs/v1.2.1-port.md); this is not full-game or long-session certification.

### iPhone and iPad

Version 1.4.3 has been played on an **iPhone 14 (iOS 26.6.1)** and an **iPad with the A16 chip (iPadOS 26.7.1)**, both signed with a personal development team in Xcode. Versions 1.0.0, 1.1.2 and 1.2.1 have not been tested on iOS.

- **First launch:** the app checks the game data (about 15 seconds) and imports the shaders it carries, with a progress bar. On an iPhone 14 this takes about 3 minutes before the title appears. Later launches show the title in about 8 seconds.
- **Picture:** the game runs in handheld mode, as a Switch does without its dock: 1280×720.
- **Shaders:** an iPhone or iPad cannot translate shaders while playing. A draw whose shader variant the app does not carry is skipped, so objects can be missing in places no player has reported yet. The device records what it missed in `shader-list-contribution.txt` (Files app › On My iPhone › Tears of the Kingdom); the game never asks about it during play. While the device is connected to the Mac that built the app (cable or the same Wi-Fi), the Mac checks every 10 minutes and every 500 new shaders asks once to send the file in a [GitHub issue](https://github.com/ly05010419/MacNativeGames/issues/new?template=shader-list.yml) (`games/totk/ios/tools/shader_contrib_watch.py`, installed by the iOS build; `--no-shader-reminder` skips it, `shader_contrib_watch.py uninstall` removes it). Once merged into the [shared shader list](docs/shader-list.md), the next build carries them for everyone. With a Mac, `games/totk/ios/tools/fill_shaders.py` also translates them and sends them to the device right away.
- **Memory:** the game uses close to 4 GB on an iPhone 14, near what the phone can give it. In busy areas iOS can close the game after several minutes of play when the phone runs out of memory (seen twice on an iPhone 14, about 8 minutes in). Reducing the game's memory on iOS is the next piece of work.

## Controls

On Mac, controllers recognized by macOS (such as DualSense and Xbox controllers) are mapped to a Switch Pro Controller. PlayStation and Xbox face buttons map by position, so each one sits where the game's prompt shows it: the right button is A, the bottom B, the top X and the left Y (Circle / Cross / Triangle / Square; Xbox B / A / Y / X). Nintendo controllers map by label. `TOTK_FACE_BUTTONS=label` maps every controller by label instead. Rumble and motion require the corresponding features to be exposed by the controller and macOS; not every controller has a gyroscope. Mac keyboard mappings use physical key positions corresponding to a US layout:

| Key | Switch | | Key | Switch |
|---|---|---|---|---|
| W A S D | left stick | | arrow keys | right stick |
| J | A | | K, Space | B |
| I | X (jump) | | U | Y (attack) |
| Q / E | L / R | | Option / Shift | ZL / ZR |
| F T H G | d-pad left / up / right / down | | Z / C | left / right stick press |
| Return | + (menu) | | Tab, Esc | − (map) |

Controller layout (Mac): by default the face buttons follow the Switch layout by position: the right button is A, the bottom B, the top X and the left Y on every controller (Nintendo controllers by their labels, which are the same; `TOTK_FACE_BUTTONS=label` maps PlayStation and Xbox controllers by label instead). To change any button, put lines `physical = Switch [mode]` in `~/Documents/NSW Games/Tears of the Kingdom/Settings/controls.txt`; lines not given keep the default, `#` starts a comment and `none` leaves a button unused. Physical buttons are named by position: `south` `east` `west` `north` (face buttons), `l1` `r1` `l2` `r2` `l3` `r3`, `menu` (Options / Menu), `view` (Create / View), `up` `down` `left` `right` (d-pad) and `touchpad` (the touchpad click of a DualSense or DualShock 4, unused by default: `touchpad = minus` opens the Purah Pad). Switch buttons: `a` `b` `x` `y` `l` `r` `zl` `zr` `plus` `minus` `lstick` `rstick` `up` `down` `left` `right`. The optional mode says how long the Switch button is held: `hold` (default) while the physical button is down, `tap` for 400 ms after each press, and `toggle` on and off at each press, cleared when the left stick returns to center after moving, and `aim <fire button>` while the button is down except while the fire button is also down: `l2 = zr aim r2` draws the bow with L2 and shoots with R2, and R2's own line applies only while L2 is up. The default is the same as this file, which maps by position:

```
east = a
south = b
north = x
west = y
```

To run without holding a button, two lines can drive the run button: `south = b tap` (tap the bottom button again and again, as in GTA) or `l3 = b toggle` (click the stick once and move; it stops when the stick returns to center). The Switch's B is contextual: while climbing, a held B lets go of the wall, so a toggle that is still on when you climb drops the player. Release the stick before climbing, or use `tap`.

The launcher checks the file at every start and names the first bad line in an alert. Delete the file for the default layout.

Mac-only extra keys: **1** show or hide all on-screen overlays (FPS, shader count, system load and the shortcut reminder, together in one small box in the window's top-right corner; `TOTK_HUD=corners` puts the readings back in the picture's corners), **2** frame interpolation (off → 30+30 → half the refresh rate → unlimited), **3** cycle picture mode (MetalFX 2K → native 2K → off), **4** ultrawide 21:9 on / off (1.2.1 and 1.4.3: the camera sees more at the sides and the picture fills a wider window; `TOTK_ASPECT=32:9` or `3440x1440` picks another shape and starts with it on; the HUD, menus and map keep their shape in the middle 16:9 of the window, the map with black bars at the sides; markers that follow objects in the world are not moved out yet). The shortcut reminder shows the current interpolation and picture modes and refreshes twice a second in both HUD layouts. MetalFX is off by default and only starts when you select its mode with **3**; resizing the window never enables it automatically. Text entry (naming a horse) uses the Mac keyboard. iPhone/iPad uses touch controls or a compatible game controller and does not show the Mac 1/2/3/4 shortcut prompts. The touch layout follows a Switch controller: L and ZL at the top left, R and ZR at the top right, − and + between them, the d-pad on the left and A, B, X, Y on the right. The d-pad sits above the left stick and A, B, X, Y above the right stick, and L3 and R3 (stick presses) are in the bottom corners. Touch the left half away from the buttons and drag for the left stick, the right half for the camera (right stick); each stick is centred where your finger goes down. With a game controller connected the touch controls are hidden: touch the screen to show and use them, and they hide again 3 seconds after you lift your fingers.

Native 2K changes the game's composition canvas and UI to 2560×1440. The 3D scene still follows the game's separate render scaling; this does not force every internal render target to 1440p. The manual MetalFX mode also works in smaller windows and scales to the actual output size, including 4K.

Language: the game and the launcher's own text (startup screen, shader warm-up progress, overlays, keyboard prompt) follow the Mac's language. The launcher text is Chinese on a Chinese Mac and English otherwise. In a 1.4.3 build, Brazilian Portuguese selects the game's official pt-BR text. To give only this game another language, set its preferred language, for example `defaults write org.macnativegames.totk AppleLanguages -array pt-BR` (`defaults delete org.macnativegames.totk AppleLanguages` removes it). To override, launch with `TOTK_LANG` for the game (for example `open --env TOTK_LANG=en-US "Tears of the Kingdom.app"`) or `TOTK_UI_LANG=zh` / `en` for the launcher text.

## Where things are

Mac player data is separate from the app, under `~/Documents/NSW Games/Tears of the Kingdom/` or `~/Documents/NSW Games/Breath of the Wild/`:

| Folder | Contents |
|---|---|
| `Saves` | Game saves; back these up before changing versions. |
| `Shaders Cache` | Prepared and runtime shader variants, compiled libraries and pipeline state. |
| `Screenshots` | Screenshots saved by the native host. |
| `Settings` | Per-game settings, including `controls.txt`. |
| `Mods` | Version-specific RomFS mods. |
| `Logs` | `game.log` and `game.previous.log`. |
| `Cache` | Temporary storage, data-verification state and runtime command files. |

The launcher creates these folders and, on first preparation, copies legacy saves, settings, mods and shader caches when available. Original legacy data remains in place. If the new game folder already exists, it is used as-is; check your saves before removing any old folder. The TOTK contribution file is `shader-list-contribution.txt` in the game's Documents folder. iOS continues to use its app's Documents container.

TOTK mods live in `Mods/<v0|v112|v121|v143>`. Their `manifest.tsv` lists each replaced or added RomFS file as tab-separated path, size, SHA-256 and original SHA-256 (`-` for a new file); files sit under the same relative paths. `python3 games/totk/scripts/make_mod_manifest.py <mods folder> --app <Tears of the Kingdom.app>` creates the manifest. Paths are case-sensitive. ExeFS mods are unsupported, and a changed or mismatching mod stops startup or execution. Move a mod out of this folder to disable it.

## Known issues

- TOTK supports base 1.0.0, or base plus the separate 1.1.2, 1.2.1 or 1.4.3 update NSP/NSZ. BOTW currently requires base 1.0.0 plus update 1.9.0 on Mac; its DLC is optional and needs both add-on content NSPs (`--dlc` twice). Other versions, combined packages, compressed base games (NSZ/XCZ) and personalized tickets remain unsupported. On iPhone and iPad only TOTK 1.4.3 has been tested; places no player has reported can show missing objects, and iOS can close the game when memory runs out (see [iPhone and iPad](#iphone-and-ipad)).
- No online features, no amiibo.
- New shaders can cause compilation stutters. Cache reuse reduces these, but does not guarantee stutter-free gameplay.
- The existing source tests cover generated container/update fixtures, relocation formats, work-folder isolation, packaging, desktop migration, shader extraction (BNSH, agl `.sharcb` and game-module shaders), the shared shader list and the players' contribution file, first-import cache preservation, the Metal toolchain lookup and 27 native RomFS path cases. GitHub Actions is configured to run the source tests and the native path parser under the undefined-behavior sanitizer, without game data or keys. A complete Mac build also passed with a local XCI-derived NSP fixture and generated common ticket. An independent retail base NSP and gameplay from that input have not yet been tested.

## How it works

See [docs/how-it-works.md](docs/how-it-works.md).

Recorded performance experiments, remaining work and comparison methods: [Mac performance follow-ups](docs/performance-followups.md).

## Acknowledgements

- **[MacNativeGames](https://github.com/ly05010419/MacNativeGames)** (MIT) by Yong Li and contributors: this fork's base, including the native Tears of the Kingdom port, the build tools and everything listed below.
- **[d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile)** (MIT) developed this way of running Switch games natively (for Diablo II: Resurrected): the NSO rewriter, the Horizon host and the NVN-on-Metal layer here started as its generic parts.
- **[nxnative](https://github.com/seedds/nxnative)**: the one-script packaging and NSP common-ticket import follow its approach.
- **Ryujinx** (MIT): its shader translator (`vendor/ryujinx`) turns the game's Maxwell shaders into SPIR-V, and its system-service code is a reference. With this project's patches and service it is compiled ahead of time (.NET NativeAOT) into `games/totk/prebuilt/macos-arm64/totk_xlate`; `games/totk/tools/totk_xlate/publish_native.sh` rebuilds it (needs the .NET 10 SDK, only for developers).
- **[SPIRV-Cross](https://github.com/KhronosGroup/SPIRV-Cross)** (Apache-2.0) turns SPIR-V into Metal Shading Language; it is fetched at a pinned commit and compiled with clang during the build.
- **deko3d** (zlib): texture layout sizes. **bcdec**: BC texture decoding. **libopus** and **libvpx** (BSD) from Homebrew: audio and the pre-rendered movies.

## License

Project additions and the combined work use **GPL-3.0-only** with one additional term under GPLv3 section 7(b): forks and derived works must keep the attribution notice in [NOTICE](NOTICE), crediting NSWMacPort and MacNativeGames, in their documentation. See [LICENSE](LICENSE). Distributed derivatives must comply with GPLv3, including its corresponding-source requirements when distributing binaries. Upstream code keeps its applicable license and notices; the complete prior MIT grant is preserved in [LICENSE.upstream-MIT](LICENSE.upstream-MIT) and remains valid for the portions it covers. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). This license does not cover Nintendo's game content.

## Export a clean source package

From a Git checkout, create a local archive without publishing anything:

```sh
python3 tools/build_source_release.py /path/to/new/NSWMacPort-source.tar.gz
```

The destination directory must exist and the output file must be new. The exporter uses current tracked files and nonignored new source files, so local changes are included. It excludes `.git`, game containers, extracted or rewritten game files, firmware fonts, keys, saves, generated shader caches, builds, logs, test directories and symlinks. It retains the open-source runtime and build tools, license notices, compatibility hashes and shader identifiers, plus the translator whose SHA-256 is pinned in the exporter. Unknown binaries and recognized Nintendo containers or key assignments are rejected. A sorted SHA-256 manifest and normalized archive metadata make repeated exports of unchanged inputs reproducible. Repository tests remain in the checkout; exporting does not delete or change them.

The filters are not a copyright-provenance audit of arbitrary new text files or patch instruction encodings. Review new source before public distribution, and preserve all upstream notices. Generated game apps, private work folders and the user's Documents data are never release artifacts.
