# Changelog: Super Smash Bros. Ultimate 13.0.5 online port

## Where the folder comes from

This port began on 2026-10-23 as a copy of `games/odyssey` (each game carries its own runtime, CONTRIBUTING), adapted
to Super Smash Bros. Ultimate 13.0.5 (`main` SHA-256 `eb79e639…dc52d`; NEX 4.6.13, Pia 5.19.1). Copies of other games'
tools and where they came from: [third_party/COPIES.md](third_party/COPIES.md).

## Build

- `scripts/build_ssbu.py`: one command from the base NSP, the 13.0.5 update, keys and firmware to `SSBU.app`. Each step
  resumes from its verified output; partial folders of an interrupted step are removed.
- The program modules come from the update, the RomFS is the base merged with the update, and the fighters' NRO modules
  are extracted from `data.arc` (`scripts/extract_nros.py`) and rewritten like the program. Every input and output is
  checked against `assets/expected-13.0.5.json`.
- The launcher (`scripts/ssbu_app_launcher.m`) and the app bundle (`scripts/package_native_app.py`) keep everything the
  game writes in "SSBU Data" next to the app; the last five sessions' saves are backed up before each start.

## Online (ssbu-oof port standard v0.2)

- **Identity (`nn::account`):** the ID token comes from the server's `/v1/token` with a revocable device key; the
  password is never stored. `GetNickname` returns the account's user name online, so other players see it.
- **Network and names (`nn::nifm`, `nn::nsd`, `GetAddrInfo`):** the network is available while a server is set; `%` in
  host patterns becomes `lp1` in both resolvers (Pia passes `nncs1-%.n.n.srv.nintendo.net` unexpanded); the game
  server name goes to the configured server, the two NAT check names to the addresses the server announces in
  `/v1/status`, and TCP 443 to the server's port.
- **TLS (`nn::ssl`):** the game's TLS connections are made by the Mac's TLS stack against the configured server, with the
  system store or the server's own CA; Nintendo's imported PKI is ignored.
- **Sockets (`nn::socket`):** `GetLastErrno` returns Horizon's numbers, which are Linux's. Pia drops a session on any
  number it does not know, and macOS's EAGAIN (35) was one: that was the "connection error" after matchmaking. UDP
  sockets bind the port the game asks for, or a free one when it is taken.
- **Friends (`nn::friends`):** the list comes from `/v1/friends` at the game's first read and every 16 s; changes reach
  the game through its notification queue, so friends' Battle Arenas show up.
- **Services met on the way:** the profanity filter (`nn::ngc`, naming an arena), the eShop calls (`nn::ec`, answered
  without the shop applet), `RequestToRelaunchApplication` (the process restarts itself in place).

## Fixes

- **Shaders shared between instances:** a variant is translated again only when no usable Metal source is on disk, and
  Metal's pipeline-archive folders are written under a lock, so two instances on one Mac no longer corrupt each other's
  files.
- **Mob Smash** crashed with `std::out_of_range`: the six default Miis were all one gender, and the mode takes three of
  each. Three of them are women now.
- **Labo VR** stopped on `nn::pctl::CheckStereoVisionPermission`; it is answered from the local stereo restriction.
- **Rumble** went to one controller: each player and motor is its own channel now.
- **Launcher screens** use the window's height: lists and tabs grow with it, screens without a list are centred.
- **Session limit removed:** the launcher copied from Odyssey closed the game after 20 minutes
  ("SSBU Data/session-minutes.txt"), a guard against GPU driver panics in that renderer's long runs. Long sessions were
  verified by hand on another Mac, so the launcher now waits for the game with no time limit and ignores that file.

## Testing several instances on one Mac

`OOF_VIRTUAL_INDEX=<n>` gives an instance its own virtual network address, so two or three instances on one Mac look like
separate players to the server (with `trust_client_address` on a development ssbu-oof server only). Two instances
cannot use local wireless on one Mac: Pia's local mode uses the fixed UDP port 12345.
