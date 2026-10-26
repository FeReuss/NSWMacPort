# Copies from other games in this repository

Every game other than Tears of the Kingdom / Breath of the Wild carries its own copy of what it builds with, so a change
to another game never changes this one. These were copied on 2026-10-23 from the commit the port was tested on
(daf270e, 2026-10-22); update them here, by hand, when SSBU needs a newer version.

| Folder | Copied from | Used by |
|---|---|---|
| `d2r/` (MIT, see its LICENSE) | `games/by3/third_party/d2r` | `scripts/build_ssbu.py`, `scripts/rewrite_nros.py` (patch_nso.py, the ahead-of-time ARM64 rewrite) |
| `totk-scripts/` | `games/totk/scripts`: extract_exefs, extract_romfs, extract_shared_fonts | `build_ssbu.py`, `extract_code.py`, `inspect_input.py`, `extract_app_icon.py` |
| `by3-scripts/` | `games/by3/scripts`: merge_romfs (with its extract_exefs / extract_romfs) | `merge_romfs.py` |
| `../tools/totk_xlate/` (MIT, see its LICENSE) | `games/totk/tools/totk_xlate` at `1b60feb`; SSBU disables non-constant texture offsets so the existing translator lowers them before SPIR-V emission | `publish_native.sh`, using the repository's `vendor/ryujinx` subset and the copied patches |
| `../prebuilt/macos-arm64/` | Built from SSBU's `tools/totk_xlate` with .NET SDK 10.0.301 / NativeAOT, using `tools/totk_xlate/publish_native.sh`; see its README | `Makefile` verifies SHA-256 and installs `shader-service/totk_xlate` |

The runtime and the graphics layer (`../runtime`, `d2r_gfx/`) came with the copy of `games/odyssey` this port started from
(2026-10-23); nothing here reads another game's folder.
