# Super Smash Bros. Ultimate 13.0.5 native Mac port — online

Super Smash Bros. Ultimate **13.0.5**, the game's final update, running natively on Apple silicon Macs, built from
**your own copy** of the game, with **online play** through [ssbu-oof](https://github.com/FeReuss/ssbu-oof) servers.
The game's own ARM64 code (the program and the fighters' NRO modules) is rewritten ahead of time and runs directly on
the Mac's CPU: no interpreter, no JIT, no CPU emulator. System services are answered by a small native host, and the
Switch's NVN graphics are rebuilt on Metal, with the game's GPU shaders translated to Metal.

**No game content is in this repository, and none will ever be.** You need a legal dump of your own game and its
13.0.5 update, and keys from your own Switch. We do not help anyone obtain keys or game files.

This folder carries its own copy of everything it builds and runs with (runtime, graphics layer, rewriter, shader
translator, scripts); nothing outside `games/ssbu/13.0.5_online` is read. The offline port of 13.0.2 is
[`../13.0.2`](../13.0.2/README.md). How this port was done: [CHANGELOG.md](CHANGELOG.md).

## Online play

The unmodified game talks to an [ssbu-oof](https://github.com/FeReuss/ssbu-oof) server with its own network protocols
(NEX, Pia). The port provides the console services those protocols sit on, as the
[ssbu-oof port standard v0.2](https://github.com/FeReuss/ssbu-oof/blob/main/docs/STANDARD.md) describes: accounts and ID
tokens, network availability, name resolution, TLS, sockets with Horizon's error numbers, and friends. Any port or
emulator that follows the same standard can play with this one.

**Test server:** `76.13.169.59:8090`, without TLS (add it in the launcher with "Secure connection" off). It is a
temporary server for trying the port: it can be reset or go away at any time, and accounts, friends and records on it
can be lost. For regular play, run your own: an ssbu-oof server is one binary and a config file
([how to run one](https://github.com/FeReuss/ssbu-oof/blob/main/docs/DEPLOY.md)).

## Status

Tested on Apple silicon with macOS 26, the game played online between players on a public ssbu-oof server.

| Works | Details |
|---|---|
| Launcher | play offline, or pick a server (add, edit, delete; status and ping), sign in or create an account (user name = in-game name, "Remember me"), Friends and Requests, add DLC |
| Quickplay | matchmaking by your rules, matches, results and rematches against other players |
| Battle Arenas | create an arena, its Arena ID, search by ID, join, play, friends' arenas |
| Friends | the friend list in the game (friends' arenas), requests and blocks from the launcher |
| Shared Content | post replays, browse the tabs, download and play, like |
| Online record | disconnects and conduct are kept by the server, as on the console |
| Fights | Smash with many fighters and stages, items; local multiplayer with several Mac controllers (rumble per player) |
| Games & More | Century, All-Star and Cruel Smash; Labo VR shows its notice and waits for handheld mode |
| DLC fighters | from your own DLC NSPs (licences only): "Add DLC…" in the launcher, or drop the files or a folder on the server list |
| Saves | kept next to the app; the saves of the last five sessions are backed up |
| Sound and movies | game audio (Opus), VP9 movies |
| Long sessions | no time limit; long sessions verified by hand on another Mac |

| Pending | Details |
|---|---|
| Online Tourneys | the server does not support them yet (ssbu-oof) |
| Elite Smash | Smash power and Elite matchmaking: not in the server yet |
| Spectator | needs Elite Smash on the server |
| Startup shaders | there is no whole-game shader seed yet: shaders are translated while playing, so the first matches stutter |
| Local wireless | two instances on one Mac cannot play (Pia's fixed UDP port); two Macs not tested |
| GameCube controllers | not supported |
| iPhone / iPad | no build |

## Build

Needs a Mac with Apple silicon and macOS 26 or later, Xcode with the Metal toolchain
(`xcodebuild -downloadComponent MetalToolchain`), Python 3.14, `git`, and from Homebrew `opus libvpx`.

```sh
cd games/ssbu/13.0.5_online
python3.14 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/build_ssbu.py --base "<your base game>.nsp" --update "<your 13.0.5 update>.nsp" \
    --keys "<your prod.keys>" --firmware "<unpacked firmware NCA folder>"
```

- The base game is the v0 NSP (`01006A800016E000`), the update 13.0.5 (`01006A800016E800`); the firmware folder gives
  the system fonts. Every input and output is checked against `assets/expected-13.0.5.json` (hashes only).
- The first build takes a while (the merged RomFS, the code rewrite, the fighters' modules); a second build reuses
  every verified step, and an interrupted one resumes.
- Everything goes to the work folder `SSBU_build` (`--work` to choose another): it holds game data, so never share or
  commit it. Git ignores it.
- The app is `SSBU_build/SSBU.app`. Saves, the shader cache, the log, the server list and the DLC list are in
  `SSBU_build/SSBU Data`, next to the app; rebuilding keeps them.

## Play

Start `SSBU.app`, then **Play Offline** or **Play on a Server**.

Keys: WASD the left stick, arrow keys the right stick, J is A, K or Space is B, I is X, U is Y, Q / E are L / R,
Option / Shift are ZL / ZR, Return is +, Tab or Escape is −, T F G H the D-pad, Z / C press the sticks. Game controllers
recognized by macOS work as Pro Controllers, one player each.

Without the launcher the runtime reads the server from the environment: `OOF_SERVER` (host:port), `OOF_TLS` (1 by
default), `OOF_CA` (a PEM file trusted for that server), `OOF_PID`, `OOF_DEVICE_KEY`, `OOF_USERNAME` (from the server's
`/v1/login`). Diagnostics are off unless switched on: `OOF_NET_TRACE=1` logs the game's network calls.

## Tests

```sh
cd games/ssbu/13.0.5_online
.venv/bin/python -m unittest discover -s tests
```

They cover the launcher's server list, accounts and friends, the network host, the online client, input and rumble,
the shader cache shared between instances, the Mii and shop services and the movie callbacks; each runs its probes
without game data.

## Credits

- [jam1garner/smash-arc](https://github.com/jam1garner/smash-arc) (MIT): reference for the data.arc format read by
  `scripts/extract_nros.py`; no code was copied.
- [ssbu-oof](https://github.com/FeReuss/ssbu-oof) (MIT): the server and the port standard.
- Copies from other games in this repository, with their origin: [third_party/COPIES.md](third_party/COPIES.md).
