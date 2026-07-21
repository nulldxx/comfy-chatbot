import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crypto_key import VolumeLockedError, derive_passphrase, effective_passphrase


class TestDerivePassphrase(unittest.TestCase):
    def test_matches_pinned_sha256_definition(self):
        # The derivation is pinned: sha256(secret_key || 0x00 || password) hex.
        expected = hashlib.sha256(b"sekret\x00hunter2").hexdigest()
        self.assertEqual(derive_passphrase("sekret", "hunter2"), expected)

    def test_deterministic(self):
        self.assertEqual(
            derive_passphrase("sk", "pw"), derive_passphrase("sk", "pw")
        )

    def test_changes_with_password(self):
        self.assertNotEqual(
            derive_passphrase("sk", "pw1"), derive_passphrase("sk", "pw2")
        )

    def test_changes_with_secret_key(self):
        self.assertNotEqual(
            derive_passphrase("sk1", "pw"), derive_passphrase("sk2", "pw")
        )

    def test_separator_prevents_collisions(self):
        # Without a separator ("ab","c") and ("a","bc") would collide.
        self.assertNotEqual(
            derive_passphrase("ab", "c"), derive_passphrase("a", "bc")
        )

    def test_fixed_width_hex(self):
        self.assertEqual(len(derive_passphrase("sk", "pw")), 64)


class TestEffectivePassphrase(unittest.TestCase):
    def test_returns_secret_key_when_no_password_set(self):
        # Bootstrap: exactly the historic behaviour before any UI password.
        self.assertEqual(
            effective_passphrase("SECRET", password_is_set=False, password=None),
            "SECRET",
        )
        # Even if a password happens to be in memory, an unset flag means bootstrap.
        self.assertEqual(
            effective_passphrase("SECRET", password_is_set=False, password="pw"),
            "SECRET",
        )

    def test_returns_derived_when_password_set(self):
        got = effective_passphrase("SECRET", password_is_set=True, password="pw")
        self.assertEqual(got, derive_passphrase("SECRET", "pw"))

    def test_never_equals_secret_key_once_set(self):
        got = effective_passphrase("SECRET", password_is_set=True, password="pw")
        self.assertNotEqual(got, "SECRET")

    def test_raises_when_set_but_no_password_in_memory(self):
        # Password set but nobody logged in since restart -> locked, not a fallback.
        with self.assertRaises(VolumeLockedError):
            effective_passphrase("SECRET", password_is_set=True, password=None)
        with self.assertRaises(VolumeLockedError):
            effective_passphrase("SECRET", password_is_set=True, password="")


if __name__ == "__main__":
    unittest.main()
