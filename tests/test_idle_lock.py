"""Tests for the idle session lock: after a period with no authenticated request and
no running job, the app logs everyone off and closes the encrypted volumes.

The timer itself is driven through idle_lock.check_now(now=...) with an injected
clock, so nothing here sleeps.
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


class TestIdleLockTimer(unittest.TestCase):
    """The pure timer/activity logic in idle_lock."""

    def setUp(self):
        idle_lock._reset_for_tests()
        self.fired = []

    def tearDown(self):
        # Put the app's real wiring back so later tests in the same process aren't
        # left with a disabled (or fake-callback) idle lock.
        idle_lock.configure(app_module.IDLE_TIMEOUT_SECONDS,
                            app_module._idle_lock_down, app_module._idle_busy)

    def _configure(self, timeout=100, on_idle=None, is_busy=None):
        def default_on_idle():
            self.fired.append(True)
            return True
        idle_lock.configure(timeout, on_idle or default_on_idle, is_busy)
        # configure() stamps time.time(); pin the clock to a known origin.
        idle_lock._last_activity = 1000.0

    def test_fires_once_the_timeout_has_elapsed(self):
        self._configure(timeout=100)
        self.assertFalse(idle_lock.check_now(now=1099.0))
        self.assertEqual(self.fired, [])
        self.assertTrue(idle_lock.check_now(now=1100.0))
        self.assertEqual(len(self.fired), 1)

    def test_fires_only_once_until_the_next_activity(self):
        self._configure(timeout=100)
        self.assertTrue(idle_lock.check_now(now=1200.0))
        # Still idle, but already locked down -- must not re-run.
        self.assertFalse(idle_lock.check_now(now=1300.0))
        self.assertEqual(len(self.fired), 1)

    def test_activity_resets_the_clock_and_rearms(self):
        self._configure(timeout=100)
        self.assertTrue(idle_lock.check_now(now=1200.0))
        idle_lock.mark_activity()          # user logs back in
        self.assertFalse(idle_lock._locked_down)
        idle_lock._last_activity = 2000.0
        self.assertFalse(idle_lock.check_now(now=2099.0))
        self.assertTrue(idle_lock.check_now(now=2100.0))
        self.assertEqual(len(self.fired), 2)

    def test_a_running_job_suspends_the_clock(self):
        busy = {"v": True}
        self._configure(timeout=100, is_busy=lambda: busy["v"])
        # Long past the timeout, but a job is running -> no lockdown.
        self.assertFalse(idle_lock.check_now(now=5000.0))
        self.assertEqual(self.fired, [])
        # ...and the clock was reset, so the full timeout runs from job end.
        busy["v"] = False
        self.assertFalse(idle_lock.check_now(now=5099.0))
        self.assertTrue(idle_lock.check_now(now=5100.0))

    def test_timeout_of_zero_disables_everything(self):
        self._configure(timeout=0)
        self.assertFalse(idle_lock.check_now(now=999999.0))
        self.assertEqual(self.fired, [])
        idle_lock.mark_activity()
        self.assertFalse(idle_lock._started)

    def test_failed_lockdown_is_retried_on_the_next_tick(self):
        attempts = []

        def flaky():
            attempts.append(True)
            return len(attempts) > 1   # first attempt "couldn't get the lock"

        self._configure(timeout=100, on_idle=flaky)
        self.assertFalse(idle_lock.check_now(now=1200.0))
        self.assertFalse(idle_lock._locked_down)
        self.assertTrue(idle_lock.check_now(now=1260.0))
        self.assertEqual(len(attempts), 2)

    def test_raising_callbacks_never_escape(self):
        def boom():
            raise RuntimeError("agent down")

        self._configure(timeout=100, on_idle=boom)
        self.assertFalse(idle_lock.check_now(now=1200.0))

        idle_lock._reset_for_tests()

        def busy_boom():
            raise RuntimeError("jobs_lock exploded")

        self._configure(timeout=100, is_busy=busy_boom)
        # A broken busy check must not trigger a lockdown mid-generation.
        self.assertFalse(idle_lock.check_now(now=1200.0))
        self.assertEqual(self.fired, [])


class TestIdleBusy(unittest.TestCase):
    """app._idle_busy reads the real job registry."""

    def setUp(self):
        self._orig = dict(app_module.jobs)
        app_module.jobs.clear()

    def tearDown(self):
        app_module.jobs.clear()
        app_module.jobs.update(self._orig)

    def test_false_when_no_jobs(self):
        self.assertFalse(app_module._idle_busy())

    def test_false_when_all_jobs_terminal(self):
        app_module.jobs["a"] = {"status": "done"}
        app_module.jobs["b"] = {"status": "error"}
        app_module.jobs["c"] = {"status": "cancelled"}
        self.assertFalse(app_module._idle_busy())

    def test_true_while_a_job_runs(self):
        app_module.jobs["a"] = {"status": "done"}
        app_module.jobs["b"] = {"status": "running"}
        self.assertTrue(app_module._idle_busy())


class TestIdleLockDown(unittest.TestCase):
    """app._idle_lock_down drives the agent and drops the credentials."""

    def setUp(self):
        self._orig_archive = app_module.ARCHIVE_VOLUME
        self._orig_output = app_module.OUTPUT_VOLUME
        # The auth epoch is process-global and a lockdown bumps it permanently, which
        # would invalidate the forged sessions every other test module relies on.
        self._orig_epoch = app_module._auth_epoch
        app_module.ARCHIVE_VOLUME = "/vol/archive.img"
        app_module.OUTPUT_VOLUME = "/vol/output.img"
        auth_store.set_session_password("livepass1")

    def tearDown(self):
        app_module.ARCHIVE_VOLUME = self._orig_archive
        app_module.OUTPUT_VOLUME = self._orig_output
        app_module._auth_epoch = self._orig_epoch
        auth_store.clear_session_password()

    def _recorder(self, events):
        def fake(payload, timeout=120.0):
            events.append((payload.get("action"), payload.get("target"),
                           payload.get("volume")))
            return {"ok": True}
        return fake

    def test_closes_output_and_clears_credentials(self):
        events = []
        before = app_module.current_auth_epoch()
        with patch.object(app_module, "_agent_request", self._recorder(events)), \
             patch.object(app_module, "_host_mount_active", return_value=False):
            self.assertTrue(app_module._idle_lock_down())

        self.assertIn(("unmount", "output", "/vol/output.img"), events)
        self.assertIn(("unmount", None, "/vol/archive.img"), events)
        # No host bind was active, so it must not have been torn down.
        self.assertNotIn("host-unmount", [e[0] for e in events])
        # Credentials gone and every outstanding cookie revoked.
        self.assertIsNone(auth_store.current_password())
        self.assertNotEqual(app_module.current_auth_epoch(), before)

    def test_forces_host_unmount_when_the_host_holds_the_archive(self):
        events = []
        with patch.object(app_module, "_agent_request", self._recorder(events)), \
             patch.object(app_module, "_host_mount_active", return_value=True):
            self.assertTrue(app_module._idle_lock_down())
        self.assertIn(("host-unmount", "host", "/vol/archive.img"), events)

    def test_defers_without_clearing_credentials_when_archive_is_busy(self):
        events = []
        before = app_module.current_auth_epoch()
        app_module.archive_lock.acquire()
        try:
            with patch.object(app_module, "_agent_request", self._recorder(events)):
                self.assertFalse(app_module._idle_lock_down())
        finally:
            app_module.archive_lock.release()
        # Nothing touched: the retry on the next tick must find a consistent state.
        self.assertEqual(events, [])
        self.assertEqual(auth_store.current_password(), "livepass1")
        self.assertEqual(app_module.current_auth_epoch(), before)

    def test_agent_failures_do_not_block_the_logoff(self):
        def broken(payload, timeout=120.0):
            raise RuntimeError("agent socket refused")

        with patch.object(app_module, "_agent_request", broken), \
             patch.object(app_module, "_host_mount_active", return_value=False):
            self.assertTrue(app_module._idle_lock_down())
        self.assertIsNone(auth_store.current_password())

    def test_skips_volume_work_when_encryption_is_disabled(self):
        app_module.ARCHIVE_VOLUME = ""
        app_module.OUTPUT_VOLUME = ""
        events = []
        with patch.object(app_module, "_agent_request", self._recorder(events)):
            self.assertTrue(app_module._idle_lock_down())
        self.assertEqual(events, [])
        self.assertIsNone(auth_store.current_password())


class TestAuthEpochInvalidatesSessions(unittest.TestCase):
    """A bumped epoch bounces existing cookies back to /login, including in
    bootstrap mode where no UI password has been set."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        self._orig_epoch = app_module._auth_epoch

    def tearDown(self):
        app_module._auth_epoch = self._orig_epoch

    def test_session_without_epoch_is_accepted_before_any_lockdown(self):
        # Pre-upgrade cookies carry no auth_epoch and must keep working.
        with self.client.session_transaction() as s:
            s["authenticated"] = True
        with patch.object(auth_store, "password_is_set", return_value=False):
            r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 200)

    def test_stale_epoch_is_rejected(self):
        with self.client.session_transaction() as s:
            s["authenticated"] = True
            s["auth_epoch"] = app_module.current_auth_epoch()
        app_module.bump_auth_epoch()
        with patch.object(auth_store, "password_is_set", return_value=False):
            r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])


if __name__ == "__main__":
    unittest.main()
