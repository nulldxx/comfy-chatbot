"""archive_browse — hold the encrypted archive volume open while /archive-explore browses it.

The archive volume is unmounted at rest: every other operation (api_archive,
api_fscheck) mounts it inside a single request and unmounts it in a ``finally``.
Browsing can't work that way. A folder listing, every thumbnail, and every slide of
a slideshow is a separate HTTP request, and ``zuluCrypt-cli`` open/close per image
would be both glacial and a good way to wear out the header.

So the explorer takes a *lease* instead: the first request mounts the volume and
every subsequent one renews the lease. When the lease expires (nobody has browsed
for ``timeout`` seconds) the registered ``on_expire`` callback fires and app.py
unmounts the volume again, returning it to the closed-at-rest state that /fscheck
and the idle lockdown expect.

Design notes:

- **A lease, not a held archive_lock.** _lock_down() acquires archive_lock
  *non-blocking* and gives up if it's busy, so a browse session that held the lock
  for the minutes a user spends looking at pictures would defer the idle lockdown
  indefinitely — the exact thing that lock exists to permit. The lock is taken only
  for the short mount/unmount calls; the lease is separate state.
- **A watchdog thread, not a re-armed threading.Timer.** A slideshow renews the
  lease every few seconds as it loads the next slide; re-arming a Timer would spawn
  a thread per image. Same reasoning as idle_lock, and the same shape.
- **Started lazily, never at import.** gunicorn.conf.py sets ``preload_app = True``,
  so this module is imported in the master *before* the fork and threads do not
  survive fork(). touch() calls ensure_started(), which creates the watchdog in the
  worker that actually serves requests.
- **No import of app.** app wires itself in through configure(), so the dependency
  runs one way and this module is unit-testable without a Flask app.

The volume can also be closed out from under us — an archive op, an fsck, a host
mount for samba, or an idle lockdown all unmount it. Each of those calls
note_closed(), which disarms the lease so the watchdog doesn't unmount a second
time; the client simply re-opens on its next request.

One Gunicorn worker with shared threads (see gunicorn.conf.py), so module globals
guarded by a lock are process-wide — the same assumption idle_lock and auth_store
make.
"""

import threading
import time

# How often the watchdog re-checks. Well below any sane timeout, and cheap: one
# comparison per tick when nothing is open.
TICK_SECONDS = 30

_lock = threading.Lock()

_timeout_seconds = 0
_on_expire = None

_last_use = 0.0
_open = False
_started = False


def configure(timeout_seconds, on_expire):
    """Wire up the browse lease. Called once from app.py at import time.

    ``timeout_seconds`` <= 0 disables the auto-close, leaving the volume open until
    something else closes it (the Close button, an archive op, or the idle
    lockdown). ``on_expire`` is called with no arguments when the lease runs out and
    must return truthy on success — a falsy return means "couldn't do it now" and
    the tick is retried, exactly as idle_lock's on_idle does."""
    global _timeout_seconds, _on_expire, _last_use, _open
    with _lock:
        _timeout_seconds = int(timeout_seconds or 0)
        _on_expire = on_expire
        _last_use = time.time()
        _open = False


def touch():
    """Record a browse request, renewing the lease and marking the volume open.

    Called by app.py after a successful mount, and again on every archive-browse
    request that follows, so the lease tracks actual use rather than the age of the
    session."""
    global _last_use, _open
    with _lock:
        _last_use = time.time()
        _open = True
    ensure_started()


def ensure_started():
    """Start the watchdog thread if it isn't running. Idempotent and cheap."""
    global _started
    with _lock:
        if _started or _timeout_seconds <= 0:
            return
        _started = True
    thread = threading.Thread(target=_watchdog, name="archive-browse", daemon=True)
    thread.start()


def note_closed():
    """Record that the volume is already closed, disarming the lease.

    Called by app.py from every site that unmounts the archive — the Close button,
    api_archive's finally, the fsck job, host-mount, and _lock_down() — so the
    watchdog doesn't fire afterwards and unmount a volume that isn't mounted. That
    would be harmless in itself, but it would log a misleading auto-close and, worse,
    could pop a *host* bind that `m` had established in the meantime."""
    global _open
    with _lock:
        _open = False


def is_open():
    """True if a browse lease is currently held (backs /api/archive-browse/status)."""
    with _lock:
        return _open


def check_now(now=None):
    """Run a single lease check. Returns True if the auto-close fired on this call.

    Split out from the watchdog loop so tests can drive it with an injected clock
    instead of sleeping."""
    global _open

    if now is None:
        now = time.time()

    with _lock:
        if _timeout_seconds <= 0 or not _open or _on_expire is None:
            return False
        if now - _last_use < _timeout_seconds:
            return False

    # Call on_expire outside the lock: it takes archive_lock and talks to the agent
    # over a socket. Holding _lock across that would block every browse request for
    # the duration of an unmount, and invites deadlock against note_closed().
    fired = False
    try:
        fired = bool(_on_expire())
    except Exception:
        fired = False

    if fired:
        with _lock:
            _open = False
    return fired


def _watchdog():
    while True:
        time.sleep(TICK_SECONDS)
        try:
            check_now()
        except Exception:
            # Never let the watchdog thread die — it has no supervisor.
            pass


def _reset_for_tests():
    """Restore module state to its pre-configure() defaults (tests only)."""
    global _timeout_seconds, _on_expire, _last_use, _open, _started
    with _lock:
        _timeout_seconds = 0
        _on_expire = None
        _last_use = 0.0
        _open = False
        _started = False
