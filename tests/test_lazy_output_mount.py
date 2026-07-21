"""Tests for the deferred (mount-on-first-login) output volume, the counterpart to
the password-derived re-key: once a login password is set the output volume no longer
auto-mounts at startup and is unlocked on first login instead."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import auth_store
import agent_client
from app import app


class TestEntrypointDefersWhenPasswordSet(unittest.TestCase):
    """agent_client (run by the container entrypoint) must skip the output
    mount/check when a UI password is set — there's no password at startup."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_auth_file = auth_store.AUTH_FILE
        auth_store.AUTH_FILE = Path(self.tmp.name) / ".auth.json"

    def tearDown(self):
        auth_store.AUTH_FILE = self._orig_auth_file
        self.tmp.cleanup()

    def _env(self):
        # Point at a socket that does not exist: if the code tried to contact the
        # agent instead of skipping, it would fail (return 1), not return 0.
        return patch.dict(os.environ, {
            "OUTPUT_VOLUME": str(Path(self.tmp.name) / "out.img"),
            "ARCHIVE_AGENT_SOCKET": str(Path(self.tmp.name) / "nope.sock"),
            "SECRET_KEY": "sk",
        })

    def test_mount_output_skips_when_password_set(self):
        auth_store.save_password_hash("somepass1")
        with self._env():
            self.assertEqual(agent_client._mount_output(), 0)

    def test_check_output_skips_when_password_set(self):
        auth_store.save_password_hash("somepass1")
        with self._env():
            self.assertEqual(agent_client._check_output(), 0)

    def test_mount_output_still_attempts_when_no_password(self):
        # No password set -> not deferred -> it tries the (absent) agent and fails.
        with self._env():
            self.assertEqual(agent_client._mount_output(), 1)


class TestLazyOutputMount(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.images_dir = Path(self.tmp.name) / "images"
        self.images_dir.mkdir()

        self._orig_auth_file = auth_store.AUTH_FILE
        auth_store.AUTH_FILE = Path(self.tmp.name) / ".auth.json"
        auth_store.save_password_hash("livepass1")
        auth_store.set_session_password("livepass1")

        self._orig_output = app_module.OUTPUT_VOLUME
        self._orig_images = app_module.IMAGES_DIR
        self._orig_secret = app_module.SECRET_KEY
        self._orig_fscheck = app_module.OUTPUT_FSCHECK_RESULT
        app_module.OUTPUT_VOLUME = str(Path(self.tmp.name) / "out.img")
        app_module.IMAGES_DIR = self.images_dir
        app_module.SECRET_KEY = "sk"
        app_module.OUTPUT_FSCHECK_RESULT = Path(self.tmp.name) / "fscheck.json"

    def tearDown(self):
        auth_store.AUTH_FILE = self._orig_auth_file
        auth_store.clear_session_password()
        app_module.OUTPUT_VOLUME = self._orig_output
        app_module.IMAGES_DIR = self._orig_images
        app_module.SECRET_KEY = self._orig_secret
        app_module.OUTPUT_FSCHECK_RESULT = self._orig_fscheck
        self.tmp.cleanup()

    def _recorder(self, events):
        def fake(payload, timeout=120.0):
            events.append(payload.get("action"))
            if payload.get("action") == "mount":
                # Real agent drops the marker at the volume root (IMAGES_DIR) on mount.
                (self.images_dir / app_module.OUTPUT_MARKER).write_text("x")
            if payload.get("action") == "fsck":
                return {"ok": True, "clean": True}
            return {"ok": True}
        return fake

    def test_mounts_and_writes_fscheck_result(self):
        events = []
        with patch.object(app_module, "_agent_request", self._recorder(events)):
            app_module._lazy_output_check_and_mount()
        # It fscks (unmounted) then mounts, and the marker is now visible.
        self.assertIn("fsck", events)
        self.assertIn("mount", events)
        self.assertLess(events.index("fsck"), events.index("mount"))
        self.assertTrue(app_module._output_already_mounted())
        # The output fscheck result was recorded for /api/fscheck to surface.
        self.assertTrue(app_module.OUTPUT_FSCHECK_RESULT.exists())

    def test_uses_derived_passphrase(self):
        from crypto_key import derive_passphrase
        seen = {}

        def fake(payload, timeout=120.0):
            if payload.get("action") == "mount":
                seen["pw"] = payload.get("password")
                (self.images_dir / app_module.OUTPUT_MARKER).write_text("x")
            return {"ok": True, "clean": True}

        with patch.object(app_module, "_agent_request", fake):
            app_module._lazy_output_check_and_mount()
        self.assertEqual(seen["pw"], derive_passphrase("sk", "livepass1"))

    def test_idempotent_when_already_mounted(self):
        # Marker already present -> already mounted -> no agent calls.
        (self.images_dir / app_module.OUTPUT_MARKER).write_text("x")
        events = []
        with patch.object(app_module, "_agent_request", self._recorder(events)):
            app_module._lazy_output_check_and_mount()
        self.assertEqual(events, [])

    def test_noop_when_no_password_set(self):
        auth_store.clear_session_password()
        # Delete the hash file so password_is_set() is False.
        Path(auth_store.AUTH_FILE).unlink()
        events = []
        with patch.object(app_module, "_agent_request", self._recorder(events)):
            app_module._lazy_output_check_and_mount()
        self.assertEqual(events, [])


class TestLoginPrimesPassword(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_auth_file = auth_store.AUTH_FILE
        self._orig_pw = auth_store.PASSWORD
        self._orig_user = app_module.USERNAME
        auth_store.AUTH_FILE = Path(self.tmp.name) / ".auth.json"
        auth_store.PASSWORD = "bootpass1"
        app_module.USERNAME = "user"
        auth_store.clear_session_password()

    def tearDown(self):
        auth_store.AUTH_FILE = self._orig_auth_file
        auth_store.PASSWORD = self._orig_pw
        app_module.USERNAME = self._orig_user
        auth_store.clear_session_password()
        self.tmp.cleanup()

    def test_login_sets_in_memory_password_and_triggers_mount(self):
        with patch.object(app_module, "_start_lazy_output_mount") as start:
            r = self.client.post("/login", data={"username": "user", "password": "bootpass1"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(auth_store.current_password(), "bootpass1")
        start.assert_called_once()

    def test_stale_cookie_after_restart_forces_relogin(self):
        # Password set, but no in-memory password (simulates a restart with a still
        # valid signed cookie) -> login_required must bounce back to /login.
        auth_store.save_password_hash("realpw12")
        auth_store.clear_session_password()
        with self.client.session_transaction() as s:
            s["authenticated"] = True
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])


if __name__ == "__main__":
    unittest.main()
