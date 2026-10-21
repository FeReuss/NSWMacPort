"""The play-test summary counts world frames only and reports the long-frame shares PERFORMANCE.md compares."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'games/botw'))
import session


class SessionSummary(unittest.TestCase):
    def test_world_frames_and_shares(self):
        rows = ['# header', 'frame\tname\tvalue']
        for frame in range(1, 201):
            t, draws = (30 if frame <= 100 else 61 + frame / 60), (500 if frame <= 100 else 3000)
            ms = 120.0 if frame in (150, 151) else 16.0
            rows += [f'{frame}\t@frame_ms\t{ms}', f'{frame}\t@t_s\t{t}', f'{frame}\t@draws\t{draws}',
                     f'{frame}\t@core_wait_max_ms\t{60.0 if ms > 50 else 1.0}']
        with tempfile.TemporaryDirectory() as tmp:
            tsv = Path(tmp) / 'frames.tsv'
            tsv.write_text('\n'.join(rows) + '\n')
            figures = session.summary(tsv)
        self.assertEqual((figures['frames'], figures['over50'], figures['over100'], figures['core_wait_slow']), (100, 2.0, 2.0, 60.0))
        self.assertEqual(figures['p50'], 16.0)
        better = dict(figures, fps=figures['fps'] + 3, over33=figures['over33'] - 1.5)
        self.assertEqual(session.changes(better, figures), 'fps +3.0 p90 +0.0 p99 +0.0 over33 -1.5 over50 +0.0')


class SaveCopy(unittest.TestCase):
    def test_profile_holds_copies_and_the_originals_stay(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / 'data'
            (data / 'Settings').mkdir(parents=True); (data / 'Settings/account.uid').write_text('uid')
            (data / 'Saves/user/0').mkdir(parents=True); (data / 'Saves/user/0/game_data.sav').write_text('save')
            with patch.object(session, 'DATA', data):
                profile = session.save_copy(Path(tmp) / 'session')
            (profile / 'save/user/0/game_data.sav').write_text('changed by an experiment')
            self.assertEqual((profile / 'account.uid').read_text(), 'uid')
            self.assertEqual((data / 'Saves/user/0/game_data.sav').read_text(), 'save')


class StallSummary(unittest.TestCase):
    def test_old_and_new_report_formats(self):
        lines = [
            '[totk-thread] [core-stall] h=0x1004 prio 30 mask 0x2 waited 20.1 ms: core1 h=0x1008 prio 44 held 21.0 ms, 0 SVCs, pc main+0xef637c lr main+0xef6318;',
            '[totk-thread] [core-stall] h=0x1004 got core 1 after 35.5 ms',
            '[totk-thread] [core-stall] h=0x1018 prio 46 mask 0x2 waited 20.4 ms after svc 0x18 from main+0x1234 (+3 unreported) participant 0: '
            'core1 h=0x1008 prio 44 held 40.2 ms, 0 SVCs, pc main+0xef6384 lr main+0xef6318 jobq 0x2a0001000 taken 0/12 flag 1 participant 1;',
        ]
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'game.log'
            log.write_text('\n'.join(lines) + '\n')
            waiters, owners, participants, waited = session.stalls(log)
        self.assertEqual(waiters[('0x1018', 46, '0x2', 'main+0x1234')], 1)
        self.assertEqual(waiters[('0x1004', 30, '0x2', '?')], 1)
        self.assertEqual(owners[('core1', '0x1008', 44, 'no SVC', 'main+0xef63xx')], 2)
        self.assertEqual(participants, {('0x1008', 1, '0x2a0001000'): 1, ('0x1018', 0, 'waiting'): 1})
        self.assertEqual(waited['0x1004'], 35.5)


if __name__ == '__main__':
    unittest.main()
