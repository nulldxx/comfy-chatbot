import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth_store


class TestAuthStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth_file = Path(self.tmp.name) / ".auth.json"
        # Redirect the module's storage + bootstrap env password for the test.
        self._orig_file = auth_store.AUTH_FILE
        self._orig_pw = auth_store.PASSWORD
        auth_store.AUTH_FILE = self.auth_file
        auth_store.PASSWORD = "envpass"

    def tearDown(self):
        auth_store.AUTH_FILE = self._orig_file
        auth_store.PASSWORD = self._orig_pw
        self.tmp.cleanup()

    # --- Bootstrap: no file yet -> env password is authoritative ---------------
    def test_bootstrap_no_file(self):
        self.assertFalse(auth_store.password_is_set())
        self.assertTrue(auth_store.verify_password("envpass"))
        self.assertFalse(auth_store.verify_password("wrong"))
        self.assertFalse(auth_store.verify_password(None))

    # --- Once set, the stored hash supersedes the env password -----------------
    def test_set_supersedes_env(self):
        auth_store.save_password_hash("newpass1")
        self.assertTrue(auth_store.password_is_set())
        self.assertTrue(auth_store.verify_password("newpass1"))
        # Old env password must NO LONGER work.
        self.assertFalse(auth_store.verify_password("envpass"))
        self.assertFalse(auth_store.verify_password("wrong"))

    # --- Stored file holds a scrypt hash, not the plaintext --------------------
    def test_file_stores_hash_not_plaintext(self):
        auth_store.save_password_hash("secretpw123")
        data = json.loads(self.auth_file.read_text())
        self.assertIn("password_hash", data)
        self.assertTrue(data["password_hash"].startswith("scrypt$"))
        self.assertNotIn("secretpw123", data["password_hash"])
        self.assertIn("updated_at", data)

    # --- Salt is random: same password hashes differently ----------------------
    def test_salt_is_random(self):
        auth_store.save_password_hash("samepw123")
        first = json.loads(self.auth_file.read_text())["password_hash"]
        auth_store.save_password_hash("samepw123")
        second = json.loads(self.auth_file.read_text())["password_hash"]
        self.assertNotEqual(first, second)
        self.assertTrue(auth_store.verify_password("samepw123"))

    # --- File is written with 0600 permissions ---------------------------------
    def test_file_permissions_0600(self):
        auth_store.save_password_hash("permcheck1")
        mode = stat.S_IMODE(os.stat(self.auth_file).st_mode)
        self.assertEqual(mode, 0o600)

    # --- Corrupt / unparseable file behaves as "not set" -----------------------
    def test_corrupt_file_falls_back_to_bootstrap(self):
        self.auth_file.write_text("not json")
        self.assertFalse(auth_store.password_is_set())
        self.assertTrue(auth_store.verify_password("envpass"))


if __name__ == "__main__":
    unittest.main()
