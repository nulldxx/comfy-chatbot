"""idle_lock — re-lock the appliance after a period of user inactivity.

Once a UI login password is set, the LUKS passphrase for the encrypted volumes is
derived from it and the plaintext is held in memory for the life of the process
(auth_store._session_password). Without this module that memory copy is only ever
dropped by a container restart, so a box left alone overnight sits with the output
volume decrypted and mounted and a valid session cookie in the browser.

Here we keep a single "last activity" timestamp and a watchdog thread. After
``timeout`` seconds with no activity and nothing running, the registered ``on_idle``
callback fires; app.py uses it to close the volumes, forget the password and
invalidate every outstanding session cookie.

Design notes:

- **A watchdog thread, not a re-armed threading.Timer.** generation_service's
  auto-purge re-arms a Timer per event, which is right there because generations
  start and finish rarely. Activity here fires on *every* authenticated request, and
  re-arming a Timer spawns a thread each time; a single thread that ticks every
  TICK_SECONDS makes mark_activity() a plain timestamp write.
- **Started lazily, never at import.** gunicorn.conf.py sets ``preload_app = True``,
  so this module is imported in the master *before* the fork, and threads do not
  survive fork(). mark_activity() calls ensure_started(), which guarantees the
  watchdog is created in the worker that actually serves requests.
- **No import of app.** app wires itself in through configure(), so the dependency
  runs one way and this module is unit-testable without a Flask app.

One Gunicorn worker with shared threads (see gunicorn.conf.py), so module globals
guarded by a lock are process-wide — the same assumption auth_store makes.
"""

import threading
import time

# How often the watchdog re-checks. Well below any sane timeout, and cheap: one
# comparison plus (at most) an is_busy() call per tick.
TICK_SECONDS = 60

_lock = threading.Lock()

_timeout_seconds = 0
_on_idle = None
_is_busy = None

_last_activity = 0.0
_started = False
_locked_down = False


def configure(timeout_seconds, on_idle, is_busy=None):
    """Wire up the idle lock. Called once from app.py at import time.

    ``timeout_seconds`` <= 0 disables the feature entirely. ``on_idle`` is called
    with no arguments when the timeout expires and must return truthy on success —
    a falsy return means "couldn't do it now" and the tick is retried. ``is_busy``,
    when given, suppresses the timeout while it returns True (a long generation
    keeps the session alive)."""
    global _timeout_seconds, _on_idle, _is_busy, _last_activity, _locked_down
    with _lock:
        _timeout_seconds = int(timeout_seconds or 0)
        _on_idle = on_idle
        _is_busy = is_busy
        _last_activity = time.time()
        _locked_down = False


def mark_activity():
    """Record user activity, resetting the idle clock.

    Called from login_required (so it can never be reset by the unauthenticated
    Docker healthcheck polling /health) and on successful login."""
    global _last_activity, _locked_down
    with _lock:
        if _timeout_seconds <= 0:
            return
        _last_activity = time.time()
        _locked_down = False
    ensure_started()


def ensure_started():
    """Start the watchdog thread if it isn't running. Idempotent and cheap."""
    global _started
    with _lock:
        if _started or _timeout_seconds <= 0:
            return
        _started = True
    thread = threading.Thread(target=_watchdog, name="idle-lock", daemon=True)
    thread.start()


def check_now(now=None):
    """Run a single idle check. Returns True if the lockdown fired on this call.

    Split out from the watchdog loop so tests can drive it with an injected clock
    instead of sleeping."""
    global _last_activity, _locked_down

    if now is None:
        now = time.time()

    with _lock:
        if _timeout_seconds <= 0 or _locked_down or _on_idle is None:
            return False
        busy_fn = _is_busy
        idle_for = now - _last_activity
        timeout = _timeout_seconds

    # Call the callbacks outside the lock: is_busy() takes generation_service's
    # jobs_lock and on_idle() takes archive_lock / output_mount_lock and talks to
    # the agent over a socket. Holding _lock across those invites deadlock and
    # would block every request for the duration of an unmount.
    if busy_fn is not None:
        try:
            if busy_fn():
                with _lock:
                    _last_activity = now
                return False
        except Exception:
            # A broken busy check must not cause a lockdown mid-generation.
            return False

    if idle_for < timeout:
        return False

    fired = False
    try:
        fired = bool(_on_idle())
    except Exception:
        fired = False

    if fired:
        with _lock:
            _locked_down = True
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
    global _timeout_seconds, _on_idle, _is_busy, _last_activity, _started, _locked_down
    with _lock:
        _timeout_seconds = 0
        _on_idle = None
        _is_busy = None
        _last_activity = 0.0
        _started = False
        _locked_down = False
