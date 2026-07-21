"""Integration tests for the archive-agent's keyslot ops (add-key / remove-key /
header-backup) against a throwaway loopback LUKS file — never the real volumes.

Requires root + cryptsetup (keyslot ops attach a loop device), so it SKIPS in the
normal unit-test/CI environment and is meant to be run manually on the host:

    sudo python -m pytest tests/test_agent_rekey.py -v
"""

import importlib.util
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_PATH = os.path.join(REPO, "packaging", "agent", "archive-agent")

HAVE_CRYPTSETUP = shutil.which("cryptsetup") is not None
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def _load_agent():
    spec = importlib.util.spec_from_file_location("archive_agent_mod", AGENT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(HAVE_CRYPTSETUP and IS_ROOT,
                     "needs root + cryptsetup (loopback LUKS)")
class TestAgentKeyslotOps(unittest.TestCase):
    def setUp(self):
        self.agent = _load_agent()
        self.tmp = tempfile.TemporaryDirectory()
        self.volume = os.path.join(self.tmp.name, "vol.img")
        self.header_dir = os.path.join(self.tmp.name, "headers")
        self.cfg = dict(self.agent.DEFAULTS, HEADER_BACKUP_DIR=self.header_dir)

        # A small LUKS volume keyed with passphrase "A".
        with open(self.volume, "xb") as fh:
            fh.truncate(20 * 1024 * 1024)
        err = self.agent._cryptsetup_format(self.volume, "A")
        self.assertIsNone(err, err)

    def tearDown(self):
        self.tmp.cleanup()

    def _opens(self, pw):
        return self.agent._cryptsetup_test_passphrase(self.volume, pw)

    def test_add_then_remove_key(self):
        # A opens, B does not (yet).
        self.assertTrue(self._opens("A"))
        self.assertFalse(self._opens("B"))

        # add A -> B: both open.
        r = self.agent.handle_add_key(self.cfg, {
            "volume": self.volume, "password": "A", "new_password": "B"})
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["added"])
        self.assertTrue(self._opens("A"))
        self.assertTrue(self._opens("B"))

        # add again is idempotent (B already opens -> no new slot).
        r = self.agent.handle_add_key(self.cfg, {
            "volume": self.volume, "password": "A", "new_password": "B"})
        self.assertTrue(r["ok"])
        self.assertFalse(r["added"])

        # remove A, keep B: only B opens now.
        r = self.agent.handle_remove_key(self.cfg, {
            "volume": self.volume, "password": "A", "keep_password": "B"})
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["removed"])
        self.assertFalse(self._opens("A"))
        self.assertTrue(self._opens("B"))

        # removing an already-absent key is idempotent.
        r = self.agent.handle_remove_key(self.cfg, {
            "volume": self.volume, "password": "A", "keep_password": "B"})
        self.assertTrue(r["ok"])
        self.assertFalse(r["removed"])

    def test_remove_refuses_to_drop_last_key(self):
        # keep_password must open the volume first, else we refuse (never leave the
        # volume with no working key).
        r = self.agent.handle_remove_key(self.cfg, {
            "volume": self.volume, "password": "A", "keep_password": "does-not-open"})
        self.assertFalse(r["ok"])
        self.assertTrue(self._opens("A"))  # untouched

    def test_remove_refuses_same_key(self):
        r = self.agent.handle_remove_key(self.cfg, {
            "volume": self.volume, "password": "A", "keep_password": "A"})
        self.assertFalse(r["ok"])
        self.assertTrue(self._opens("A"))

    def test_add_key_wrong_old_password_fails(self):
        r = self.agent.handle_add_key(self.cfg, {
            "volume": self.volume, "password": "WRONG", "new_password": "B"})
        self.assertFalse(r["ok"])
        self.assertFalse(self._opens("B"))

    def test_header_backup_produces_restorable_0600_file(self):
        r = self.agent.handle_header_backup(self.cfg, {"volume": self.volume})
        self.assertTrue(r["ok"], r)
        backup = r["backup"]
        self.assertTrue(os.path.exists(backup))
        self.assertEqual(stat.S_IMODE(os.stat(backup).st_mode), 0o600)
        # The backup restores cleanly (a real LUKS header for this volume).
        proc = subprocess.run(
            ["cryptsetup", "luksHeaderRestore", "--batch-mode", self.volume,
             "--header-backup-file", backup],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(self._opens("A"))


if __name__ == "__main__":
    unittest.main()
