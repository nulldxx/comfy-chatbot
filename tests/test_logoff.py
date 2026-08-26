"""Tests for the on-demand lockdown: /logoff (POST /api/logoff) and the /logout link.

Both close the encrypted volumes, forget the in-memory login password and revoke every
session cookie -- the same work the idle timeout does, without the wait. The agent is
mocked throughout (there is no real LUKS volume in a test run), so what's asserted here
is the decision-making: what gets sent, and when the whole thing is refused instead.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import idle_lock
import app as app_module
import auth_store
from app import app


class LogoffTestBase(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self._orig_archive = app_module.ARCHIVE_VOLUME
        self._orig_output = app_module.OUTPUT_VOLUME
        # The auth epoch is process-global and a lockdown bumps it permanently, which
        # would invalidate the forged sessions every other test module relies on.
        self._orig_epoch = app_module._auth_epoch
        app_module.ARCHIVE_VOLUME = "/vol/archive.img"
        app_module.OUTPUT_VOLUME = "/vol/output.img"
        auth_store.set_session_password("livepass1")
        idle_lock._reset_for_tests()

    def tearDown(self):
        app_module.ARCHIVE_VOLUME = self._orig_archive
        app_module.OUTPUT_VOLUME = self._orig_output
        app_module._auth_epoch = self._orig_epoch
        auth_store.clear_session_password()
        with app_module.jobs_lock:
            app_module.jobs.clear()
        idle_lock._reset_for_tests()

    def _recorder(self, events):
        def fake(payload, timeout=120.0):
            events.append((payload.get("action"), payload.get("target"),
                           payload.get("volume")))
            return {"ok": True}
        return fake

    def _sign_in(self):
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["auth_epoch"] = app_module.current_auth_epoch()

    def _running_job(self):
        with app_module.jobs_lock:
            app_module.jobs["job-1"] = {"status": "running"}


class TestApiLogoff(LogoffTestBase):
    """POST /api/logoff."""

    def test_closes_volumes_and_drops_the_credentials(self):
        self._sign_in()
        events = []
        before = app_module.current_auth_epoch()
        with patch.object(app_module, "_agent_request", self._recorder(events)), \
             patch.object(app_module, "_host_mount_active", return_value=False):
            resp = self.client.post("/api/logoff")

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["ok"])
        self.assertIn(("unmount", "output", "/vol/output.img"), events)
        self.assertIn(("unmount", None, "/vol/archive.img"), events)
        self.assertIsNone(auth_store.current_password())
        self.assertNotEqual(app_module.current_auth_epoch(), before)

    def test_the_session_is_dead_afterwards(self):
        self._sign_in()
        with patch.object(app_module, "_agent_request", self._recorder([])), \
             patch.object(app_module, "_host_mount_active", return_value=False):
            self.client.post("/api/logoff")
        follow_up = self.client.get("/")
        self.assertEqual(follow_up.status_code, 302)
        self.assertIn("/login", follow_up.location)

    def test_refuses_while_a_job_is_running(self):
        """The important negative case: a sequence run can go for hours with no
        incoming request, and unmounting the output volume under it loses its work."""
        self._sign_in()
        self._running_job()
        events = []
        before = app_module.current_auth_epoch()
        with patch.object(app_module, "_agent_request", self._recorder(events)):
            resp = self.client.post("/api/logoff")

        self.assertEqual(resp.status_code, 409)
        self.assertIn("1 job(s) still running", resp.get_json()["error"])
        # Nothing touched -- the volumes stay open and the user stays signed in.
        self.assertEqual(events, [])
        self.assertEqual(auth_store.current_password(), "livepass1")
        self.assertEqual(app_module.current_auth_epoch(), before)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_finished_jobs_do_not_block_it(self):
        self._sign_in()
        with app_module.jobs_lock:
            app_module.jobs["done-1"] = {"status": "done"}
            app_module.jobs["err-1"] = {"status": "error"}
            app_module.jobs["cancelled-1"] = {"status": "cancelled"}
        with patch.object(app_module, "_agent_request", self._recorder([])), \
             patch.object(app_module, "_host_mount_active", return_value=False):
            resp = self.client.post("/api/logoff")
        self.assertEqual(resp.status_code, 200)

    def test_refuses_while_the_archive_is_busy(self):
        self._sign_in()
        events = []
        before = app_module.current_auth_epoch()
        app_module.archive_lock.acquire()
        try:
            with patch.object(app_module, "_agent_request", self._recorder(events)):
                resp = self.client.post("/api/logoff")
        finally:
            app_module.archive_lock.release()

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(events, [])
        self.assertEqual(auth_store.current_password(), "livepass1")
        self.assertEqual(app_module.current_auth_epoch(), before)

    def test_forces_the_host_bind_off_when_samba_holds_the_archive(self):
        self._sign_in()
        events = []
        with patch.object(app_module, "_agent_request", self._recorder(events)), \
             patch.object(app_module, "_host_mount_active", return_value=True):
            self.assertEqual(self.client.post("/api/logoff").status_code, 200)
        self.assertIn(("host-unmount", "host", "/vol/archive.img"), events)

    def test_agent_failures_do_not_block_the_logoff(self):
        def broken(payload, timeout=120.0):
            raise RuntimeError("agent socket refused")

        self._sign_in()
        with patch.object(app_module, "_agent_request", broken), \
             patch.object(app_module, "_host_mount_active", return_value=False):
            resp = self.client.post("/api/logoff")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(auth_store.current_password())

    def test_requires_auth(self):
        self.assertEqual(self.client.post("/api/logoff").status_code, 302)

    def test_tells_the_watchdog_not_to_repeat_the_work(self):
        """A manual lockdown must not be re-run (and mislogged as an idle one) when
        the idle clock later runs out."""
        idle_lock.configure(100, app_module._idle_lock_down, app_module._idle_busy)
        self._sign_in()
        with patch.object(app_module, "_agent_request", self._recorder([])), \
             patch.object(app_module, "_host_mount_active", return_value=False):
            self.client.post("/api/logoff")
        self.assertTrue(idle_lock._locked_down)
        self.assertFalse(idle_lock.check_now(now=idle_lock._last_activity + 10_000))


class TestLogoutRoute(LogoffTestBase):
    """GET /logout -- the header link's no-JS fallback, which now locks down too."""

    def test_closes_the_volumes_and_redirects_to_login(self):
        self._sign_in()
        events = []
        with patch.object(app_module, "_agent_request", self._recorder(events)), \
             patch.object(app_module, "_host_mount_active", return_value=False):
            resp = self.client.get("/logout")

        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.location)
        self.assertIn(("unmount", "output", "/vol/output.img"), events)
        self.assertIsNone(auth_store.current_password())

    def test_refuses_rather_than_signing_out_of_an_open_appliance(self):
        """Signing the user out while the volumes stay mounted is precisely the trap
        this change removes, so a refused lockdown must keep them signed in."""
        self._sign_in()
        self._running_job()
        events = []
        with patch.object(app_module, "_agent_request", self._recorder(events)):
            resp = self.client.get("/logout")

        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("/login", resp.location)
        self.assertEqual(events, [])
        self.assertEqual(auth_store.current_password(), "livepass1")
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_unauthenticated_just_goes_to_login(self):
        resp = self.client.get("/logout")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.location)


if __name__ == "__main__":
    unittest.main()
