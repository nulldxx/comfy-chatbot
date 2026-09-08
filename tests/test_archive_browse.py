"""Tests for the archive browse lease: /archive-explore holds the encrypted archive
volume mounted between requests, and a watchdog closes it again once nobody has
browsed for a while.

The lease is driven through archive_browse.check_now(now=...) with an injected
clock, so nothing here sleeps — the same approach tests/test_idle_lock.py takes.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import archive_browse
import app as app_module


class TestArchiveBrowseLease(unittest.TestCase):
    """The pure lease/expiry logic in archive_browse."""

    def setUp(self):
        archive_browse._reset_for_tests()
        self.fired = []

    def tearDown(self):
        # Put the app's real wiring back so later tests in the same process aren't
        # left with a disabled (or fake-callback) lease.
        archive_browse.configure(app_module.ARCHIVE_BROWSE_TIMEOUT_SECONDS,
                                 app_module._archive_browse_expired)

    def _configure(self, timeout=100, on_expire=None):
        def default_on_expire():
            self.fired.append(True)
            return True
        archive_browse.configure(timeout, on_expire or default_on_expire)
        # configure() stamps time.time(); pin the clock to a known origin.
        archive_browse._last_use = 1000.0

    # -- the lease is only armed once something opens it ----------------------

    def test_closed_until_touched(self):
        self._configure()
        self.assertFalse(archive_browse.is_open())
        # Nothing is mounted, so an expired clock must not unmount anything.
        self.assertFalse(archive_browse.check_now(now=99999.0))
        self.assertEqual(self.fired, [])

    def test_touch_opens_the_lease(self):
        self._configure()
        archive_browse.touch()
        self.assertTrue(archive_browse.is_open())

    # -- expiry ---------------------------------------------------------------

    def test_does_not_fire_before_the_timeout(self):
        self._configure(timeout=100)
        archive_browse.touch()
        archive_browse._last_use = 1000.0
        self.assertFalse(archive_browse.check_now(now=1099.0))
        self.assertEqual(self.fired, [])
        self.assertTrue(archive_browse.is_open())

    def test_fires_once_the_timeout_passes(self):
        self._configure(timeout=100)
        archive_browse.touch()
        archive_browse._last_use = 1000.0
        self.assertTrue(archive_browse.check_now(now=1101.0))
        self.assertEqual(self.fired, [True])
        # And the lease is disarmed, so it cannot fire twice.
        self.assertFalse(archive_browse.is_open())
        self.assertFalse(archive_browse.check_now(now=9999.0))
        self.assertEqual(self.fired, [True])

    def test_a_browse_request_renews_the_lease(self):
        """The whole point: a slideshow loading its next slide keeps the volume open."""
        self._configure(timeout=100)
        archive_browse.touch()
        archive_browse._last_use = 1000.0
        self.assertFalse(archive_browse.check_now(now=1099.0))
        archive_browse._last_use = 1099.0          # what touch() does on a request
        self.assertFalse(archive_browse.check_now(now=1198.0))
        self.assertEqual(self.fired, [])

    def test_zero_timeout_disables_auto_close(self):
        self._configure(timeout=0)
        archive_browse.touch()
        self.assertFalse(archive_browse.check_now(now=999999.0))
        self.assertEqual(self.fired, [])

    # -- being closed from underneath -----------------------------------------

    def test_note_closed_disarms(self):
        """An archive op, fsck, host-mount or idle lockdown unmounts the volume and
        calls note_closed(); the watchdog must not then unmount a second time."""
        self._configure(timeout=100)
        archive_browse.touch()
        archive_browse._last_use = 1000.0
        archive_browse.note_closed()
        self.assertFalse(archive_browse.is_open())
        self.assertFalse(archive_browse.check_now(now=1101.0))
        self.assertEqual(self.fired, [])

    def test_touch_rearms_after_a_close(self):
        self._configure(timeout=100)
        archive_browse.touch()
        archive_browse.note_closed()
        archive_browse.touch()                      # the client re-opened
        self.assertTrue(archive_browse.is_open())

    # -- a callback that can't run right now ----------------------------------

    def test_falsy_callback_leaves_the_lease_armed_for_a_retry(self):
        """_archive_browse_expired returns False when archive_lock is held, so the
        close is retried on the next tick rather than silently dropped."""
        self._configure(timeout=100, on_expire=lambda: False)
        archive_browse.touch()
        archive_browse._last_use = 1000.0
        self.assertFalse(archive_browse.check_now(now=1101.0))
        self.assertTrue(archive_browse.is_open())

    def test_raising_callback_never_escapes(self):
        def boom():
            raise RuntimeError("agent unreachable")
        self._configure(timeout=100, on_expire=boom)
        archive_browse.touch()
        archive_browse._last_use = 1000.0
        self.assertFalse(archive_browse.check_now(now=1101.0))
        self.assertTrue(archive_browse.is_open())


class TestArchiveBrowseWiring(unittest.TestCase):
    """app.py's end of the contract."""

    def test_app_wires_the_lease(self):
        # configure() ran at import; the callback is the app's, not a leftover.
        self.assertIs(archive_browse._on_expire, app_module._archive_browse_expired)

    def test_expiry_is_a_noop_without_a_volume(self):
        orig = app_module.ARCHIVE_VOLUME
        app_module.ARCHIVE_VOLUME = ""
        try:
            self.assertTrue(app_module._archive_browse_expired())
        finally:
            app_module.ARCHIVE_VOLUME = orig

    def test_expiry_defers_while_archive_lock_is_held(self):
        orig = app_module.ARCHIVE_VOLUME
        app_module.ARCHIVE_VOLUME = "/host/archive.img"
        app_module.archive_lock.acquire()
        try:
            self.assertFalse(app_module._archive_browse_expired())
        finally:
            app_module.archive_lock.release()
            app_module.ARCHIVE_VOLUME = orig


if __name__ == "__main__":
    unittest.main()
