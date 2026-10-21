import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'native macOS account implementation')
class AccountListing(unittest.TestCase):
    def test_local_user_matches_handles_and_survives_a_new_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / 'account-listing'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-pthread',
                            str(ROOT / 'tests/test_account_listing.c'),
                            str(ROOT / 'games/totk/runtime/account.c'), '-o', str(executable)], check=True)
            profile = root / 'profile'
            env = dict(os.environ, TOTK_PROFILE_DIR=str(profile))
            subprocess.run([str(executable)], env=env, check=True, capture_output=True)
            identity = profile / 'account.uid'
            first = identity.read_bytes()
            self.assertEqual(identity.stat().st_mode & 0o777, 0o600)
            subprocess.run([str(executable)], env=env, check=True, capture_output=True)
            self.assertEqual(identity.read_bytes(), first)
