#!/usr/bin/env python3
"""Build SSBU.app: a self-contained, locally signed Mac app around the native runtime.

The bundle carries the runtime, shader tools, rewritten modules and fighter NROs, merged RomFS and shared fonts (large
files are APFS clones, so they take no extra space). Saves, the shader cache, the log and the ssbu-oof server list go to
"SSBU Data" next to the app (scripts/ssbu_app_launcher.m). Both stay in the ignored SSBU_build folder; the app contains
game data from the user's own dump and must not be shared. On first packaging "SSBU Data" is seeded with the work
folder's profile (SSBU_build/user-data) and the warmed shader cache; existing data is never replaced.
"""
from pathlib import Path
import os
import plistlib
import re
import shutil
import subprocess

GAME = Path(__file__).resolve().parents[1]
BUILD = Path(os.environ.get("SSBU_WORK", GAME / "SSBU_build")).resolve()   # SSBU_WORK: another build folder
APP = BUILD / "SSBU.app"
DATA = BUILD / "SSBU Data"
ICON = BUILD / "app-assets/SSBUIcon.jpg"   # optional, game-derived: never committed
CHAIN = "rt-" + re.search(r'#define DMSH_CHAIN "([^"]+)"',
                          (GAME / "third_party/d2r_gfx/gfx/nvn/dmsh_chain.h").read_text()).group(1)


def clone(source: Path, target: Path):
    """APFS copy-on-write clone (no extra space); plain copy elsewhere."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if subprocess.run(["cp", "-c", str(source), str(target)]).returncode != 0:
        shutil.copy2(source, target)


def clone_files(source: Path, target: Path):
    for item in source.iterdir():
        if item.is_file() and not item.is_symlink():
            clone(item, target / item.name)


def seed_data():
    DATA.mkdir(exist_ok=True)
    profile = DATA / "profile"
    seed = BUILD / "user-data"
    if (profile / "save").is_dir():
        print("SSBU Data already has a save; left as it is.")
    elif (seed / "save").is_dir():
        profile.mkdir(mode=0o700, exist_ok=True)
        if (seed / "account.uid").is_file():
            shutil.copy2(seed / "account.uid", profile / "account.uid")
        shutil.copytree(seed / "save", profile / "save", dirs_exist_ok=True)
        print(f"Seeded the save from {seed.relative_to(BUILD)}.")
    cache = DATA / "shader-cache" / CHAIN
    if not cache.exists() and (BUILD / "build/shader-cache" / CHAIN).is_dir():
        cache.parent.mkdir(exist_ok=True)
        subprocess.run(["cp", "-cR", str(BUILD / "build/shader-cache" / CHAIN), str(cache)], check=True)
        print("Seeded the shader cache.")
    minutes = DATA / "session-minutes.txt"
    if not minutes.exists():
        minutes.write_text("20\n")


def main():
    native = BUILD / "build/ssbu-native"
    for need in (native, BUILD / "build/shader-service/totk_xlate", BUILD / "build/spirv-cross/spirv-cross",
                 BUILD / "rewritten/main.patched.bin", BUILD / "rewritten/nro",
                 BUILD / "extracted/local/romfs/romfs.bin", BUILD / "extracted/firmware/shared-fonts"):
        if not need.exists():
            raise SystemExit(f"missing {need}; build the runtime (make runtime WORK=SSBU_build) and prepare the local data first")
    if APP.exists():
        shutil.rmtree(APP)
    contents = APP / "Contents"; macos = contents / "MacOS"; resources = contents / "Resources"; game = resources / "game"
    macos.mkdir(parents=True)
    clone(native, game / "build/ssbu-native")
    clone(BUILD / "build/shader-service/totk_xlate", game / "build/shader-service/totk_xlate")
    clone(BUILD / "build/spirv-cross/spirv-cross", game / "build/spirv-cross/spirv-cross")
    clone_files(BUILD / "rewritten", game / "rewritten")
    clone_files(BUILD / "rewritten/nro", game / "rewritten/nro")   # the runtime loads NROs from <modules>/nro (loader.c)
    os.symlink("../rewritten", game / "rewritten/local")   # the module directory the runtime is given, as in SSBU_build
    for name in ("romfs.bin", "runtime-index.bin"):
        clone(BUILD / "extracted/local/romfs" / name, game / "romfs" / name)
    for font in sorted((BUILD / "extracted/firmware/shared-fonts").glob("*.bfttf")):
        clone(font, game / "fonts" / font.name)
    slot = BUILD / "analysis/game-slot"
    if slot.is_dir():
        (game / "slot-dir.txt").write_text(str(slot) + "\n")
    os.symlink("../Resources/game/build/ssbu-native", macos / "ssbu-native")
    libs = BUILD / "build/shader-libs"
    if libs.is_dir():
        for lib in libs.glob("*.metallib"):
            clone(lib, game / "build/shader-libs" / lib.name)
        os.symlink("../Resources/game/build/shader-libs", macos / "shader-libs")
    subprocess.run(["xcrun", "clang", "-O2", "-Wall", "-Wextra", "-Werror", "-fobjc-arc", "-framework", "Cocoa", "-framework", "Security",
                    f'-DSHADER_CHAIN="{CHAIN}"', "-o", str(macos / "SSBU"), str(GAME / "scripts/ssbu_app_launcher.m")],
                   check=True)
    info = {
        "CFBundleDevelopmentRegion": "en", "CFBundleDisplayName": "Super Smash Bros. Ultimate", "CFBundleName": "SSBU",
        "CFBundleExecutable": "SSBU",
        "CFBundleIdentifier": "local.projectidea.ssbu", "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "0.1", "CFBundleVersion": "1",
        "LSApplicationCategoryType": "public.app-category.games", "LSMinimumSystemVersion": "26.0",
        "NSHighResolutionCapable": True,
        # ssbu-oof servers the player adds may be plain http (LAN, development; the game's own TLS is OOF_TLS=1)
        "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True},
        "NSLocalNetworkUsageDescription": "Online play connects to the ssbu-oof servers you add and to other players.",
    }
    if ICON.exists():
        subprocess.run(["sips", "-s", "format", "icns", str(ICON), "--out", str(resources / "SSBUIcon.icns")],
                       check=True, stdout=subprocess.DEVNULL)
        info["CFBundleIconFile"] = "SSBUIcon"
    with (contents / "Info.plist").open("wb") as file:
        plistlib.dump(info, file)
    subprocess.run(["codesign", "--force", "--sign", "-", str(APP)], check=True, stdout=subprocess.DEVNULL)
    seed_data()
    print(APP)


if __name__ == "__main__":
    main()
