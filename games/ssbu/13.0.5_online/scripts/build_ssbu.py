#!/usr/bin/env python3
"""Build SSBU.app from your own Super Smash Bros. Ultimate dump (base NSP + 13.0.5 update NSP) in one command.

    .venv/bin/python scripts/build_ssbu.py --base BASE.nsp --update UPDATE_13.0.5.nsp --keys prod.keys \
        --firmware <unpacked firmware NCA directory> [--work SSBU_build]

Steps (each is skipped when its verified output already exists, so a failed run can simply be started again):
 1. check the inputs (title, NCA count; prints no keys or game data)
 2. extract the program modules (main, sdk, subsdk0, rtld) from the update and merge the base + update RomFS
 3. extract the system fonts from the firmware (an NSP carries none)
 4. rewrite the ARM64 modules ahead of time (third_party/d2r/patch_nso.py, native ruleset)
 5. extract the fighters' NRO modules from data.arc and rewrite them too
 6. loader tables: verifies ExeFS, rewrite, NROs and RomFS against assets/expected-13.0.5.json
 7. shader tools (pinned SPIRV-Cross) and the runtime
 8. extract the game's icon and package <work>/SSBU.app, with saves, shader cache and logs in "<work>/SSBU Data"

Shaders are translated while playing (there is no whole-game seed yet), so the first matches stutter. DLC licences are
added from the app (Add DLC...). Everything written stays in the work folder, which Git ignores; it holds game data, so
never share or commit it.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

GAME_DIR = Path(__file__).resolve().parents[1]
SPIRV_CROSS = ("https://github.com/KhronosGroup/SPIRV-Cross.git", "aa217aeb6c9f0ace7a0ab233b28807edf45eb165")
MODULES = ("main", "sdk", "subsdk0")


def step(n, title):
    print(f"\n== {n}. {title}", flush=True)
    return time.monotonic()


def done(t0, note=""):
    print(f"   done in {time.monotonic() - t0:.0f} s{(' — ' + note) if note else ''}", flush=True)


def run(cmd, env=None, cwd=GAME_DIR):
    print("   $ " + " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=cwd, env={**os.environ, **(env or {})})


def link(path: Path, target: str):
    if path.is_symlink() or path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, path)


def fresh(path: Path):
    """A step's output folder without its completion marker is a partial run: remove it (it holds only derived data)."""
    if path.exists():
        print(f"   removing the partial {path.name} of an interrupted run")
        shutil.rmtree(path)


def preflight(args, py):
    problems = []
    if sys.platform != "darwin" or os.uname().machine != "arm64":
        problems.append("an Apple silicon Mac is required")
    if sys.version_info < (3, 14):
        problems.append("Python 3.14 or newer is required (data.arc uses compression.zstd)")
    for name in ("capstone", "cryptography", "lz4"):
        if subprocess.run([py, "-c", f"import {name}"], capture_output=True).returncode:
            problems.append(f"Python module {name} is missing ({py} -m pip install capstone cryptography lz4)")
    for lib in ("/opt/homebrew/opt/opus/lib/libopus.a", "/opt/homebrew/opt/libvpx/lib/libvpx.a"):
        if not Path(lib).exists():
            problems.append(f"{lib} is missing (brew install opus libvpx)")
    if not Path(args.developer_dir, "usr/bin/xcodebuild").exists():
        problems.append(f"Xcode not found at {args.developer_dir} (--developer-dir)")
    for path in (args.base, args.update, args.keys):
        if not path.is_file():
            problems.append(f"{path} not found")
    if not args.firmware.is_dir():
        problems.append(f"{args.firmware} is not a folder (unpacked firmware NCA directory)")
    # The merged RomFS (17.6 GB) is most of the ~19 GB a build needs, so a resumed build needs much less.
    need = 3 if (args.work / "extracted/local/romfs/index.json").is_file() else 20
    free = shutil.disk_usage(args.work if args.work.exists() else args.work.parent).free / 2**30
    if free < need:
        problems.append(f"only {free:.0f} GB free; about {need} GB more are needed")
    if problems:
        raise SystemExit("cannot build:\n - " + "\n - ".join(problems))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", type=Path, required=True, help="your SSBU base game NSP (v0)")
    p.add_argument("--update", type=Path, required=True, help="your SSBU 13.0.5 update NSP")
    p.add_argument("--keys", type=Path, required=True, help="prod.keys dumped from your own Switch")
    p.add_argument("--firmware", type=Path, required=True, help="unpacked firmware NCA directory, for the system fonts")
    p.add_argument("--work", type=Path, default=GAME_DIR / "SSBU_build", help="build folder (default SSBU_build)")
    p.add_argument("--developer-dir", default="/Applications/Xcode.app/Contents/Developer")
    p.add_argument("--jobs", type=int, default=os.cpu_count() or 8)
    args = p.parse_args()
    for name in ("base", "update", "keys", "firmware", "work"):
        setattr(args, name, getattr(args, name).resolve())
    py = sys.executable
    preflight(args, py)
    work, ext = args.work, args.work / "extracted"
    work.mkdir(parents=True, exist_ok=True)
    keyargs = ["--keys", args.keys]
    started = time.monotonic()

    t = step(1, "Check the inputs")
    for game in (args.base, args.update):
        run([py, "scripts/inspect_input.py", game, *keyargs])
    done(t)

    t = step(2, "Program modules and the merged RomFS")
    if (ext / "extraction-manifest.json").is_file():
        print("   modules already extracted")
    else:
        tmp = work / "exefs.partial"   # extract_code.py writes into a new folder; its files then move into extracted/
        fresh(tmp)
        run([py, "scripts/extract_code.py", args.update, *keyargs, "--output", tmp])
        ext.mkdir(exist_ok=True)
        for item in sorted(tmp.iterdir(), key=lambda f: f.name == "extraction-manifest.json"):   # the marker last
            os.replace(item, ext / item.name)
        tmp.rmdir()
    link(ext / "local/exefs", "..")
    if (ext / "local/romfs/index.json").is_file():
        print("   RomFS already merged")
    else:
        fresh(ext / "local/romfs")
        run([py, "scripts/merge_romfs.py", args.base, args.update, *keyargs, "--output", ext / "local/romfs"])
    done(t)

    t = step(3, "System fonts")
    fonts = ext / "firmware/shared-fonts"
    if (fonts / "manifest.json").is_file():
        print("   fonts already extracted")
    else:
        run([py, GAME_DIR / "third_party/totk-scripts/extract_shared_fonts.py", *keyargs, "--firmware", args.firmware,
             "--output", fonts, "--inventory", work / "analysis/firmware-inventory.json"])
    done(t)

    t = step(4, "Rewrite the ARM64 modules")
    rewritten = work / "rewritten"
    rewritten.mkdir(exist_ok=True)
    link(rewritten / "local", "../rewritten")
    for name in MODULES:
        if (rewritten / f"{name}.patched.bin").is_file():
            print(f"   {name} already rewritten")
            continue
        run([py, GAME_DIR / "third_party/d2r/patch_nso.py", ext / name, rewritten, name],
            env={"DM_RULESET": "native", "DM_PRIVATE_GATES": "0"})
    done(t)

    t = step(5, "Fighter NRO modules")
    if (ext / "nro/manifest.json").is_file():
        print("   NROs already extracted")
    else:
        fresh(ext / "nro")
        run([py, "scripts/extract_nros.py", "--romfs", ext / "local/romfs", "--main", ext / "main", "--output", ext / "nro"])
    run([py, "scripts/rewrite_nros.py", "--nro", ext / "nro", "--output", rewritten / "nro", "--jobs", args.jobs])   # skips done ones
    done(t)

    t = step(6, "Loader tables (verifies every input against the pinned 13.0.5 hashes)")
    run([py, "scripts/prepare_runtime.py", "--work", work])
    done(t)

    t = step(7, "Shader tools and the runtime")
    src = work / "src/SPIRV-Cross"
    if not (src / ".git").is_dir():
        run(["git", "clone", "--quiet", SPIRV_CROSS[0], src])
    run(["git", "-C", src, "checkout", "--quiet", SPIRV_CROSS[1]])
    # make cannot handle spaces in target paths, so the work folder goes in relative to games/ssbu/13.0.5_online (the checkout
    # itself may sit in a folder with a space, as long as the work folder's own path below it has none).
    make_work = os.path.relpath(work, GAME_DIR)
    if " " in make_work:
        raise SystemExit(f"make cannot build into a folder whose path has a space: {make_work}; pick another --work")
    run(["make", f"-j{args.jobs}", "runtime", f"WORK={make_work}", f"PY={py}"], env={"DEVELOPER_DIR": args.developer_dir})
    done(t)

    t = step(8, "Package the app")
    icon = work / "app-assets/SSBUIcon.jpg"
    if not icon.is_file():   # the game's own icon, from its control data
        run([py, "scripts/extract_app_icon.py", args.update, *keyargs, "--output", icon])
    run([py, "scripts/package_native_app.py"], env={"SSBU_WORK": str(work)})
    done(t)
    print(f"\nBuilt {work / 'SSBU.app'} in {(time.monotonic() - started) / 60:.0f} min. It contains game data from your "
          "own dump: do not share it.")


if __name__ == "__main__":
    main()
