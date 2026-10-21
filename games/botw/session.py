#!/usr/bin/env python3
"""Play-test sessions for BOTW: launch the app with per-frame timing, keep each session's logs, compare sessions.

  python3 games/botw/session.py run <name> [--app PATH] [--env NAME=VALUE ...] [--save-copy]   (play, then Cmd+Q)
  python3 games/botw/session.py compare <name> [<name> ...]
Sessions are kept in ~/Documents/NSW Games/Breath of the Wild/Logs/sessions/<name>/.
"""
import argparse
from collections import Counter
import os
import re
from pathlib import Path
import shutil
import statistics
import subprocess
import time

DATA = Path(os.environ.get('NSW_GAMES_DIR', Path.home() / 'Documents/NSW Games')) / 'Breath of the Wild'
LOGS = DATA / 'Logs'


def summary(tsv):
    """World frames only (after 60 s, more than 1000 draws): the figures games/botw/PERFORMANCE.md compares."""
    frames = {}
    with open(tsv) as rows:
        for row in rows:
            parts = row.rstrip('\n').split('\t')
            if len(parts) == 3 and parts[1].startswith('@'):
                try:
                    frames.setdefault(int(parts[0]), {})[parts[1]] = float(parts[2])
                except ValueError:
                    pass
    world = [f for f in frames.values() if '@frame_ms' in f and f.get('@t_s', 0) > 60 and f.get('@draws', 0) > 1000]
    if not world:
        return None
    times = sorted(f['@frame_ms'] for f in world)
    pick = lambda p: times[min(len(times) - 1, int(len(times) * p))]
    seconds = sum(times) / 1000
    slow = [f for f in world if f['@frame_ms'] > 50]
    return dict(frames=len(times), seconds=round(seconds), fps=round(len(times) / seconds, 1), p50=pick(.5), p90=pick(.9),
                p99=pick(.99), over33=round(100 * sum(t > 33.4 for t in times) / len(times), 1),
                over50=round(100 * len(slow) / len(times), 1), over100=round(100 * sum(t > 100 for t in times) / len(times), 1),
                core_wait_slow=round(statistics.median([f.get('@core_wait_max_ms', 0) for f in slow]), 1) if slow else 0)


STALL = re.compile(r'\[core-stall\] h=(0x[0-9a-f]+) prio (\d+) mask (0x[0-9a-f]+) waited [\d.]+ ms(?: after svc \S+ from (\S+))?'
                   r'(?: \(\+\d+ unreported\))?(?: participant (\d+))?:')
OWNER = re.compile(r'core(\d) h=(0x[0-9a-f]+) prio (\d+) held [\d.]+ ms, (\d+) SVCs, pc (\S+) lr \S+?(?: jobq (0x[0-9a-f]+) taken (\d+)/(\d+) flag (\d+) participant (-?\d+))?;')
GOT = re.compile(r'\[core-stall\] h=(0x[0-9a-f]+) got core \d+ after ([\d.]+) ms')


def stalls(log):
    """[core-stall] summary: who waited (and from which guest call), who held the core, and the job-queue participants."""
    waiters, owners, participants, waited = Counter(), Counter(), Counter(), Counter()
    for line in open(log, errors='replace'):
        if (m := GOT.search(line)):
            waited[m.group(1)] += float(m.group(2))
        elif (m := STALL.search(line)):
            waiters[(m.group(1), int(m.group(2)), m.group(3), m.group(4) or '?')] += 1
            if m.group(5):   # the waiting thread's own job-queue participant number
                participants[(m.group(1), int(m.group(5)), 'waiting')] += 1
            for core, handle, prio, svcs, pc, queue, *_rest, participant in OWNER.findall(line):
                region = re.sub(r'[0-9a-f]{2}$', 'xx', pc) if pc.startswith(('main+', 'sdk+')) else re.sub(r'\+0x.*', '', pc)
                owners[(f'core{core}', handle, int(prio), 'no SVC' if svcs == '0' else 'SVCs', region)] += 1
                if queue:
                    participants[(handle, int(participant), queue)] += 1
    return waiters, owners, participants, waited


def save_copy(folder):
    """A profile with copies of Settings/ and Saves/: an experiment plays the same game, and the real saves stay as they were."""
    profile = folder / 'profile'
    shutil.copytree(DATA / 'Settings', profile)
    shutil.copytree(DATA / 'Saves', profile / 'save')
    return profile


def run(name, app, env, copy_saves=False):
    folder = LOGS / 'sessions' / name
    if folder.exists():
        raise SystemExit(f'{folder} exists; pick another session name')
    if subprocess.run(['pgrep', '-f', 'totk-native rewritten'], capture_output=True).returncode == 0:
        raise SystemExit('close the running game first')
    if copy_saves:
        folder.mkdir(parents=True)
        env = [*env, f'TOTK_PROFILE_DIR={save_copy(folder)}']
    tsv = LOGS / 'frames.tsv'
    tsv.unlink(missing_ok=True)
    arguments = ['open', '--env', f'DM_NVN_TSV={tsv}', '--env', 'DM_NVN_TSV_CALLS=0']
    for item in env:
        arguments += ['--env', item]
    subprocess.run([*arguments, *(['-b', 'org.macnativegames.botw'] if app is None else [app])], check=True)   # the app build_app.py registered
    time.sleep(5)
    while subprocess.run(['pgrep', '-f', 'totk-native rewritten'], capture_output=True).returncode == 0:
        time.sleep(2)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LOGS / 'game.log', folder / 'game.log')
    if tsv.exists():
        shutil.move(tsv, folder / 'frames.tsv')
    (folder / 'variant.txt').write_text('\n'.join(env) + '\n')
    compare([name])


DECIDING = ('fps', 'p90', 'p99', 'over33', 'over50')   # the figures an experiment is accepted on (PERFORMANCE.md)


def changes(figures, baseline):
    """Differences from the first session in the deciding figures, e.g. 'fps +3.0 over33 -4.6'."""
    return ' '.join(f'{k} {figures[k] - baseline[k]:+.1f}' for k in DECIDING)


def compare(names):
    baseline = None
    for name in names:
        folder = LOGS / 'sessions' / name
        variant = (folder / 'variant.txt').read_text().splitlines() if (folder / 'variant.txt').exists() else []
        figures = summary(folder / 'frames.tsv') if (folder / 'frames.tsv').exists() else None
        print(name, variant or ['default'], figures or 'no world frames recorded')
        if figures and baseline:
            print('   vs', names[0] + ':', changes(figures, baseline))
        baseline = baseline or figures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    play = commands.add_parser('run')
    play.add_argument('name')
    play.add_argument('--app', type=Path, help='default: the BOTW app the last build registered')
    play.add_argument('--env', action='append', default=[], help='NAME=VALUE for the game, e.g. BOTW_CORE_TIMESLICE_MS=2')
    play.add_argument('--save-copy', action='store_true', help='play on copies of Settings/ and Saves/ (experiments)')
    report = commands.add_parser('compare')
    report.add_argument('names', nargs='+')
    stalled = commands.add_parser('stalls', help='who waited for a virtual core, who held it, job-queue participants')
    stalled.add_argument('name')
    args = parser.parse_args()
    if args.command == 'stalls':
        waiters, owners, participants, waited = stalls(LOGS / 'sessions' / args.name / 'game.log')
        print('waiters (handle, prio, mask, waiting since):', waiters.most_common(10))
        print('total wait ms by waiter:', [(h, round(ms)) for h, ms in waited.most_common(10)])
        print('owners (core, handle, prio, SVCs since taking the core, pc):', owners.most_common(10))
        print("job-queue participants (handle, participant, queue it spins on or 'waiting' for a core):", participants.most_common(12))
    else:
        run(args.name, args.app, args.env, args.save_copy) if args.command == 'run' else compare(args.names)
