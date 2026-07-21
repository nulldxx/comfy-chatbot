"""Tests for the password-derived LUKS re-key flow (/api/change-password) and the
lazy output-volume mount on first login. The host agent is replaced with a recorder
so we exercise ordering and failure-safety without cryptsetup."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import auth_store
from app import app
from crypto_key import derive_passphrase


class RekeyTestBase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)

        # Fresh auth store (bootstrap: env password authoritative until first set).
        self._orig_auth_file = auth_store.AUTH_FILE
        self._orig_pw = auth_store.PASSWORD
        auth_store.AUTH_FILE = tmp / ".auth.json"
        auth_store.PASSWORD = "bootpass1"
        auth_store.clear_session_password()

        # Two existing volume backing files so Path(v).exists() is True.
        self.archive_vol = str(tmp / "archive.img")
        self.output_vol = str(tmp / "output.img")
        Path(self.archive_vol).write_bytes(b"x")
        Path(self.output_vol).write_bytes(b"x")

        self._orig_archive = app_module.ARCHIVE_VOLUME
        self._orig_output = app_module.OUTPUT_VOLUME
        self._orig_output_pw = app_module.OUTPUT_PASSWORD
        self._orig_secret = app_module.SECRET_KEY
        app_module.ARCHIVE_VOLUME = self.archive_vol
        app_module.OUTPUT_VOLUME = self.output_vol
        app_module.OUTPUT_PASSWORD = ""
        app_module.SECRET_KEY = "compose-secret"

        # Authenticate directly (skip /login's lazy-mount thread for these tests).
        with self.client.session_transaction() as s:
            s["authenticated"] = True

    def tearDown(self):
        auth_store.AUTH_FILE = self._orig_auth_file
        auth_store.PASSWORD = self._orig_pw
        auth_store.clear_session_password()
        app_module.ARCHIVE_VOLUME = self._orig_archive
        app_module.OUTPUT_VOLUME = self._orig_output
        app_module.OUTPUT_PASSWORD = self._orig_output_pw
        app_module.SECRET_KEY = self._orig_secret
        self.tmp.cleanup()

    def _recorder(self, events, fail_on=None):
        """Return a fake _agent_request that appends (action, volume, payload) to
        `events` and replies ok, optionally failing the nth matching action."""
        counters = {}

        def fake(payload, timeout=120.0):
            action = payload.get("action")
            events.append((action, payload.get("volume"), payload))
            if action == "exists":
                return {"ok": True, "exists": True}
            if action == "status":
                return {"ok": True, "host_mounted": False}
            if fail_on and action == fail_on:
                counters[action] = counters.get(action, 0) + 1
                return {"ok": False, "error": f"simulated {action} failure"}
            return {"ok": True, "added": True, "removed": True}

        return fake


class TestFirstMigration(RekeyTestBase):
    def test_ordering_and_recovery(self):
        events = []
        saves = []
        real_save = auth_store.save_password_hash

        def recording_save(pw):
            saves.append(pw)
            events.append(("SAVE", None, None))
            real_save(pw)

        with patch.object(app_module, "_agent_request", self._recorder(events)), \
             patch.object(app_module, "save_password_hash", recording_save):
            r = self.client.post("/api/change-password", json={
                "current": "bootpass1", "new": "brandnew123", "confirm": "brandnew123",
            })

        self.assertEqual(r.status_code, 200, r.get_json())
        body = r.get_json()
        self.assertTrue(body["ok"])
        # First migration returns a one-time recovery passphrase.
        self.assertIn("recovery", body)
        self.assertTrue(body["recovery"])

        actions = [a for a, _v, _p in events]
        save_idx = actions.index("SAVE")

        # add-key / header-backup all happen BEFORE the commit; remove-key AFTER.
        for i, a in enumerate(actions):
            if a in ("header-backup", "add-key"):
                self.assertLess(i, save_idx, f"{a} must precede the password save")
            if a == "remove-key":
                self.assertGreater(i, save_idx, "remove-key must follow the save")

        # Both volumes were header-backed and re-keyed.
        for vol in (self.archive_vol, self.output_vol):
            per_vol = [a for a, v, _p in events if v == vol]
            self.assertIn("header-backup", per_vol)
            # new key + recovery key = two add-keys, and one remove-key.
            self.assertEqual(per_vol.count("add-key"), 2)
            self.assertEqual(per_vol.count("remove-key"), 1)

        # add-key uses the compose SECRET_KEY as the old key, the derived one as new.
        add_new = next(p for a, v, p in events
                       if a == "add-key" and v == self.archive_vol
                       and p["new_password"] == derive_passphrase("compose-secret", "brandnew123"))
        self.assertEqual(add_new["password"], "compose-secret")

        # remove-key drops the old SECRET_KEY, keeping the derived one.
        rem = next(p for a, v, p in events if a == "remove-key" and v == self.archive_vol)
        self.assertEqual(rem["password"], "compose-secret")
        self.assertEqual(rem["keep_password"], derive_passphrase("compose-secret", "brandnew123"))

        # Password is now set and the in-memory password updated to the new one.
        self.assertTrue(auth_store.password_is_set())
        self.assertTrue(auth_store.verify_password("brandnew123"))
        self.assertEqual(auth_store.current_password(), "brandnew123")

    def test_output_volume_uses_output_password_as_old_key(self):
        app_module.OUTPUT_PASSWORD = "outpw"
        events = []
        with patch.object(app_module, "_agent_request", self._recorder(events)):
            r = self.client.post("/api/change-password", json={
                "current": "bootpass1", "new": "brandnew123", "confirm": "brandnew123",
            })
        self.assertEqual(r.status_code, 200, r.get_json())
        # The output volume's add-key is authorised with OUTPUT_PASSWORD, not SECRET_KEY.
        out_add = [p for a, v, p in events if a == "add-key" and v == self.output_vol]
        self.assertTrue(all(p["password"] == "outpw" for p in out_add))
        arc_add = [p for a, v, p in events if a == "add-key" and v == self.archive_vol]
        self.assertTrue(all(p["password"] == "compose-secret" for p in arc_add))


class TestRekeyFailureSafety(RekeyTestBase):
    def test_add_key_failure_leaves_old_password_working(self):
        events = []
        saves = []
        real_save = auth_store.save_password_hash

        def recording_save(pw):
            saves.append(pw)
            real_save(pw)

        with patch.object(app_module, "_agent_request",
                          self._recorder(events, fail_on="add-key")), \
             patch.object(app_module, "save_password_hash", recording_save):
            r = self.client.post("/api/change-password", json={
                "current": "bootpass1", "new": "brandnew123", "confirm": "brandnew123",
            })

        self.assertEqual(r.status_code, 500)
        self.assertFalse(r.get_json()["ok"])
        # Nothing committed: no save, still bootstrap, old password still works.
        self.assertEqual(saves, [])
        self.assertFalse(auth_store.password_is_set())
        self.assertTrue(auth_store.verify_password("bootpass1"))
        # No remove-key was attempted (we failed before the commit).
        self.assertNotIn("remove-key", [a for a, _v, _p in events])

    def test_host_mounted_refuses_with_409(self):
        def fake(payload, timeout=120.0):
            if payload.get("action") == "exists":
                return {"ok": True, "exists": True}
            if payload.get("action") == "status":
                return {"ok": True, "host_mounted": True}
            return {"ok": True}

        with patch.object(app_module, "_agent_request", fake):
            r = self.client.post("/api/change-password", json={
                "current": "bootpass1", "new": "brandnew123", "confirm": "brandnew123",
            })
        self.assertEqual(r.status_code, 409)
        self.assertFalse(auth_store.password_is_set())


class TestExistenceViaAgent(RekeyTestBase):
    """The re-key must decide which volumes exist from the AGENT, not a local stat.

    In production the volume backing files live on the host and are NOT mounted into
    the container, so Path(v).exists() there is always False — the bug that made the
    re-key silently skip every volume while still committing the new password."""

    def test_rekey_runs_when_paths_absent_in_container(self):
        # Point the config at paths that do NOT exist in this (container-like) fs, so a
        # local Path.exists() would be False for both — yet the agent reports them.
        app_module.ARCHIVE_VOLUME = "/nonexistent/archive.iso"
        app_module.OUTPUT_VOLUME = "/nonexistent/output.iso"
        self.assertFalse(Path(app_module.ARCHIVE_VOLUME).exists())
        self.assertFalse(Path(app_module.OUTPUT_VOLUME).exists())

        events = []
        with patch.object(app_module, "_agent_request", self._recorder(events)):
            r = self.client.post("/api/change-password", json={
                "current": "bootpass1", "new": "brandnew123", "confirm": "brandnew123",
            })
        self.assertEqual(r.status_code, 200, r.get_json())
        # Both agent-reported-existing volumes were actually header-backed and re-keyed.
        for vol in ("/nonexistent/archive.iso", "/nonexistent/output.iso"):
            per_vol = [a for a, v, _p in events if v == vol]
            self.assertIn("header-backup", per_vol)
            self.assertIn("add-key", per_vol)
        self.assertTrue(auth_store.password_is_set())

    def test_absent_volume_is_skipped(self):
        # Archive genuinely absent (never created), output present: re-key only output.
        def fake(payload, timeout=120.0):
            action = payload.get("action")
            if action == "exists":
                return {"ok": True, "exists": payload.get("volume") == self.output_vol}
            if action == "status":
                return {"ok": True, "host_mounted": False}
            return {"ok": True, "added": True, "removed": True}

        events = []

        def recording(payload, timeout=120.0):
            events.append((payload.get("action"), payload.get("volume")))
            return fake(payload, timeout)

        with patch.object(app_module, "_agent_request", recording):
            r = self.client.post("/api/change-password", json={
                "current": "bootpass1", "new": "brandnew123", "confirm": "brandnew123",
            })
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertNotIn("header-backup", [a for a, v in events if v == self.archive_vol])
        self.assertIn("header-backup", [a for a, v in events if v == self.output_vol])
        self.assertTrue(auth_store.password_is_set())

    def test_probe_failure_aborts_before_commit(self):
        # If the agent can't report existence, fail closed: commit nothing so we never
        # leave an existing volume un-rekeyed but unlockable with the new password.
        def fake(payload, timeout=120.0):
            if payload.get("action") == "exists":
                return {"ok": False, "error": "agent unreachable"}
            return {"ok": True}

        with patch.object(app_module, "_agent_request", fake):
            r = self.client.post("/api/change-password", json={
                "current": "bootpass1", "new": "brandnew123", "confirm": "brandnew123",
            })
        self.assertEqual(r.status_code, 500)
        self.assertFalse(auth_store.password_is_set())
        self.assertTrue(auth_store.verify_password("bootpass1"))


class TestSecondChange(RekeyTestBase):
    def test_second_change_uses_derived_old_key_and_no_recovery(self):
        # Put the store in the "already migrated" state.
        auth_store.save_password_hash("firstpw12")
        auth_store.set_session_password("firstpw12")

        events = []
        with patch.object(app_module, "_agent_request", self._recorder(events)):
            r = self.client.post("/api/change-password", json={
                "current": "firstpw12", "new": "secondpw12", "confirm": "secondpw12",
            })
        self.assertEqual(r.status_code, 200, r.get_json())
        body = r.get_json()
        # No recovery key on a non-first change.
        self.assertNotIn("recovery", body)

        old_derived = derive_passphrase("compose-secret", "firstpw12")
        new_derived = derive_passphrase("compose-secret", "secondpw12")
        # Exactly one add-key per volume (no recovery), authorised by the derived old.
        for vol in (self.archive_vol, self.output_vol):
            adds = [p for a, v, p in events if a == "add-key" and v == vol]
            self.assertEqual(len(adds), 1)
            self.assertEqual(adds[0]["password"], old_derived)
            self.assertEqual(adds[0]["new_password"], new_derived)
            rem = next(p for a, v, p in events if a == "remove-key" and v == vol)
            self.assertEqual(rem["password"], old_derived)
            self.assertEqual(rem["keep_password"], new_derived)

        self.assertTrue(auth_store.verify_password("secondpw12"))
        self.assertEqual(auth_store.current_password(), "secondpw12")


if __name__ == "__main__":
    unittest.main()
