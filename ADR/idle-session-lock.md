# ADR: Re-lock the appliance after a period of user inactivity

**Status:** Implemented.
**Scope decided before build:** activity = authenticated requests **plus** a suspension
while any job runs; the host (`m`/samba) bind is **force-unmounted** on timeout; no
client-side idle timer — the browser simply lands on `/login` on its next action.

## Problem

The password-derived re-key made the LUKS passphrase for the archive and output volumes
depend on the login password (`ADR/archive-rekeying.md`). The plaintext password is held
in memory for the life of the process (`auth_store._session_password`), and the output
volume, once lazily mounted at first login, stays mounted for the container's whole life.

Nothing dropped either one short of a container restart — `/logout` deliberately keeps
the in-memory password (`app.py`, the comment there predates this change and still
describes the explicit-logout path correctly). So a machine left alone overnight sat with:

- the output volume decrypted and ext4-mounted in the host namespace,
- the derived key in RAM,
- a signed session cookie in the browser that never expires (Flask default cookie, no
  `PERMANENT_SESSION_LIFETIME`, signed with the stable `SECRET_KEY`).

The protection the re-keying bought was therefore only real while somebody was actually
using the appliance.

## Decision

After `IDLE_TIMEOUT_SECONDS` (default `7200`) with no authenticated request **and** no
running job, log everyone off, forget the in-memory password, and close both volumes.
Logging back in re-opens the output volume through the existing lazy-mount path, so
recovery costs the user exactly one login.

Two things made this cheap: `auth_store.clear_session_password()` already existed (and
was unused), and `login_required` already force-expires a session when a password is set
but none is in memory — the stale-cookie-after-restart path. An idle lockdown is
deliberately made to look exactly like a restart.

## How it was implemented

### 1. `config.py` — `IDLE_TIMEOUT_SECONDS`

`int(os.environ.get('IDLE_TIMEOUT_SECONDS', '7200'))`, next to `AUTO_PURGE_SECONDS` and
in the same inline style (there is no `env_int` helper and adding one would be a new
pattern). `0` disables the feature entirely.

### 2. `idle_lock.py` (new, stdlib-only)

Holds one activity timestamp and a watchdog daemon thread; **does not import `app`**, so
the dependency runs one way and the module is unit-testable without a Flask app. API:
`configure(timeout, on_idle, is_busy)`, `mark_activity()`, `ensure_started()`,
`check_now(now=None)`.

Two decisions worth recording:

- **A watchdog thread, not a re-armed `threading.Timer`.** `generation_service`'s
  auto-purge re-arms a Timer per event, which suits generations (rare). Activity here
  fires on *every* authenticated request and each re-arm spawns a thread, so instead one
  thread ticks every `TICK_SECONDS` (60) and `mark_activity()` is a timestamp write.
- **Started lazily, never at import.** `gunicorn.conf.py` sets `preload_app = True`, so
  the module is imported in the master *before* the fork, and threads do not survive
  `fork()`. `mark_activity()` calls `ensure_started()`, which creates the watchdog in the
  worker that actually serves requests.

`check_now()` calls `is_busy`/`on_idle` **outside** the module lock — they take
`jobs_lock`, `archive_lock`/`output_mount_lock` and talk to the agent over a socket, so
holding the lock across them would invite deadlock and stall every request for the
duration of an unmount. It sets the "already locked down" flag only when `on_idle`
returns truthy, so a deferred attempt is retried on the next tick.

The `Dockerfile` lists every Python module in an explicit `COPY` line, so `idle_lock.py`
had to be added there too. Omitting it built a perfectly healthy-looking image that
crash-looped on `import idle_lock` — caught by `./scripts/test-all`'s container health
check, not by the unit tests.

### 3. `app.py` — auth epoch

Session cookies are signed with the stable `SECRET_KEY` and carry no expiry, so there was
nothing to revoke them with. `_auth_epoch` (an int behind `_auth_epoch_lock`, with
`current_auth_epoch()` / `bump_auth_epoch()`) is stamped into the session at login and
compared in `login_required`.

This is needed **only for bootstrap mode**: once a UI password is set, clearing the
in-memory password is already enough to bounce every browser via the existing
`password_is_set() and current_password() is None` branch. With no password set that
branch never fires.

Cookies carrying no `auth_epoch` key default to `0`, the starting value, so sessions
issued before this shipped stay valid until the first lockdown.

### 4. `app.py` — `_idle_busy()`

`any(rec.get("status") not in TERMINAL_STATUSES for rec in jobs.values())` under
`jobs_lock`. Not optional: a server-driven `sequence-run` drives its loop in a daemon
thread for up to `COMFY_POLL_TIMEOUT_SECONDS` (4h) with **zero incoming requests**, so a
request-timestamp-only clock would log the user off and unmount the output volume from
under a running render. It also covers `/api/fscheck` jobs, which hold `archive_lock` for
up to `FSCK_TIMEOUT`. The auto-purge refcount was rejected as the signal because it only
tracks ComfyUI generations.

### 5. `app.py` — `_idle_lock_down()`

Returns `bool` so a deferred attempt is retried rather than leaving a half-locked state.

1. `archive_lock.acquire(blocking=False)` (the `api_host_mount` pattern) — `False` if busy.
2. If `_host_mount_active()`, agent `host-unmount` — the host bind is the only long-lived
   archive exposure; everything else unmounts in `api_archive`'s `finally`. Dropping it
   also closes the volume.
3. Belt and braces: agent `unmount` for `ARCHIVE_VOLUME`.
4. `output_mount_lock.acquire(blocking=False)`, then agent
   `{"action": "unmount", "target": "output", ...}` — the same payload
   `_lazy_output_check_and_mount` already uses to clear a stale mount.
5. `auth_store.clear_session_password()` and `bump_auth_epoch()`.

Volume work happens before the credentials are dropped so "logged off" and "volumes
closed" land together; the agent's `unmount` takes no passphrase, so the ordering is
about atomicity, not capability. Agent failures are logged but never block the logoff.

**No agent-side change was needed** — `handle_unmount` is already target-parameterised
(`archive` / `output` / `host`).

### 6. Marking activity inside `login_required`, not `before_request`

The Docker healthcheck polls `/health` unauthenticated and continuously; a blanket
`before_request` hook would reset the clock forever and the feature would never fire.
`login_required` is already on every authenticated route including the SSE stream.

### 7. `static/js/utils.js` — `parseJsonResponse`

`login_required` answers with a **302 to `/login`** for `/api/*` too, and `fetch` follows
redirects transparently, so an expired session arrived as the login page's HTML with
status 200 and surfaced as `"Server returned a non-JSON response: <!DOCTYPE html…"`.
`parseJsonResponse` now detects `r.redirected` with a final pathname of `/login` and
sends the browser there.

This is the one shared response helper, so it covers the common paths without touching
the ~58 raw `fetch` call sites, and the server contract stays 302 (not 401) — roughly 15
existing tests assert 302-to-`/login`.

## Consequences / known limitations

- **Force host-unmount can interrupt samba.** The idle clock tracks *app* activity, which
  samba traffic does not touch, so a long unattended copy over the `m` host mount can be
  cut off mid-flight. Re-running `m` restores it (it logs in, re-priming the in-memory
  password). This was the explicit choice over skipping the lockdown while host-mounted.
- **No images or sessions until login** after a lockdown — the same posture the lazy
  output mount already established for restarts.
- The two `EventSource('/api/progress/...')` streams error silently on an expired
  session. Acceptable: SSE only exists while a job runs, and a running job suspends the
  clock, so a live stream cannot be idled out.
- The auth epoch is in-memory and resets on restart. Harmless — a restart drops the
  in-memory password too, which `login_required` already treats as "log in again"
  whenever a password is set.
- Untouched pre-existing rough edge: `api_archive`/`api_host_mount` raise
  `VolumeLockedError` outside their `try` blocks, which would surface as a 500. Not newly
  reachable, since `login_required` bounces first and login re-primes the password.

## Tests

- `tests/test_idle_lock.py` (17 tests):
  - `TestIdleLockTimer` — drives `check_now(now=...)` with an injected clock (nothing
    sleeps): fires at the timeout, fires **once** until the next activity, activity
    re-arms it, a busy check suppresses it *and* resets the clock, `timeout=0` disables
    everything, a falsy `on_idle` is retried next tick, and raising callbacks never
    escape (a broken busy check must not lock down mid-generation).
  - `TestIdleBusy` — non-terminal jobs make `_idle_busy()` true.
  - `TestIdleLockDown` — the expected agent payloads with `_agent_request` patched;
    `host-unmount` only when the host holds the archive; a held `archive_lock` defers
    **without** touching credentials; agent failures still log the user off; encryption
    disabled skips all volume work.
  - `TestAuthEpochInvalidatesSessions` — a cookie with no epoch is accepted before any
    lockdown; a stale epoch gets 302 → `/login` in bootstrap mode.
- The classes that bump the epoch save and restore `app._auth_epoch`; it is process-global
  and a permanent bump would invalidate the forged sessions every other test module uses.
- Full suite: 451 Python tests (434 before) and 102 JS tests pass.

## Rollout (on $PROD_SERVER)

No agent change and no `/etc/archive-agent.conf` change — `handle_unmount` already
supports the `output` and `host` targets. The default (`7200`) applies with no compose
edit; set `IDLE_TIMEOUT_SECONDS` in
`~/dot-files/docker-compose/comfy-chatbot.yml` to tune or `0` to disable.

Worth a short soak with `IDLE_TIMEOUT_SECONDS=120`: confirm no lockdown while a
generation runs, then that an idle box logs off and closes the volumes (agent `status`
reports `open: false`), and that logging back in re-mounts output and the images reappear.
