# On-demand lockdown: `/logoff`, and making `/logout` mean it

**Status:** implemented
**Supersedes:** the "`/logout` deliberately keeps the in-memory password" decision in
`ADR/idle-session-lock.md`

## Context

`GET /logout` popped `authenticated` from the session cookie and redirected to `/login`.
That was all. It deliberately left:

- both LUKS volumes mounted (archive and output),
- the plaintext login password in memory (`auth_store._session_password`), so the
  password-derived LUKS key was still computable,
- every *other* outstanding session cookie valid — cookies are signed with the stable
  `SECRET_KEY`, carry no expiry, and `/logout` never bumped the auth epoch.

The original reasoning (recorded in the idle-lock ADR) was that the output volume stays
mounted in the host namespace anyway, so clearing the password would only break archive
ops on a single-user appliance. True as far as it went, but the result was a header
control labelled "Sign out" that looked like it secured the box and did not.

Meanwhile the behaviour that *does* secure it already existed — `_idle_lock_down()`,
fired by the `idle_lock` watchdog after `IDLE_TIMEOUT_SECONDS` (default 2h). There was
simply no way to say "I'm done, lock it now" without waiting out the clock or restarting
the container.

## Decision

Extract the lockdown, expose it on demand, and route every exit through it.

1. **`app._lock_down()`** — the former body of `_idle_lock_down()`, verbatim and
   reason-agnostic: force the host bind off if samba holds the archive, unmount both
   volumes via the agent, `clear_session_password()`, `bump_auth_epoch()`. Returns
   `False` rather than half-locking when `archive_lock`/`output_mount_lock` is held.
   `_idle_lock_down()` is now that call plus its idle-specific log line, so
   `idle_lock.configure(...)` and the existing tests were untouched.
2. **`POST /api/logoff`** (`@login_required`) — the real implementation. Busy check,
   then `_lock_down()`, then pop the session.
3. **`GET /logout`** — same sequence, kept as the no-JS/bookmark fallback.
4. **`/logoff` chat command** (`commands.js`), plus entries in `SLASH_COMMANDS`
   (`autocomplete.js`), the `/help` table and the `/settings` menu.
5. **The header link** (`templates/index.html`, `#sign-out`) is wired in `chat.js` to
   call `handleSlashCommand('/logoff')` instead of navigating, so the link and the
   command are one code path.
6. **`idle_lock.note_locked_down()`** — called at the end of `_lock_down()`.

### Refuse rather than half-do it

Two conditions block a lockdown, both answering **409** and both **leaving the session
signed in**:

- **A job is still running** (`_logoff_refusal()`, the same `TERMINAL_STATUSES` check
  `_idle_busy()` uses). A `sequence-run` drives its loop in a daemon thread for up to
  `COMFY_POLL_TIMEOUT_SECONDS` (4h) with no incoming requests; unmounting the output
  volume under it throws away everything it had left to write. The message names the
  count: *"2 job(s) still running — wait for them to finish or cancel them first."*
- **`_lock_down()` returned `False`** — an in-flight archive/fsck/mount holds a lock.

Staying signed in on refusal is the point, not an oversight: a sign-out that leaves the
volumes open is the exact failure being removed, so it must not be reachable by
triggering a refusal.

## Consequences

- **Every sign-out now costs a re-mount.** Logging back in pays the lazy output mount
  (the "🔒 Unlocking encrypted storage…" bubble, `_start_lazy_output_mount`) before
  macros/chats/images return. Signing out used to be free; it isn't any more. Accepted —
  that latency *is* the security property.
- **`bump_auth_epoch()` on every sign-out** kills sessions in other browsers too. Correct
  for a single-user appliance, and the only way to revoke an expiry-less signed cookie.
- **Tests must restore `app._auth_epoch`.** The epoch is process-global; a bump revokes
  the forged `sess['authenticated'] = True` sessions the rest of the suite relies on
  (those carry no epoch key, so they default to `0`). This bit immediately —
  `tests/test_simple.py::test_logout` began cascading failures into `TestSettingsBackup`
  the moment `/logout` started locking down, and now saves/restores the epoch like
  `tests/test_idle_lock.py` always has.
- **`note_locked_down()` was needed** or the watchdog would re-run the lockdown when the
  clock later expired — harmless work (unmounting an unmounted volume, clearing a cleared
  password) but a misleading "idle for 7200s" in the log and a second epoch bump.
- **No new Python module**, so no `Dockerfile` `COPY` change — the trap that broke the
  image when `idle_lock.py` first shipped.
- **`/logoff` is not confirmed with y/n.** A lockdown is fully recoverable by logging
  back in, and the busy check already covers the case where it would destroy work.

## Files

`app.py` (`_lock_down`, `_idle_lock_down`, `_logoff_refusal`, `api_logoff`, `logout`),
`idle_lock.py` (`note_locked_down`), `static/js/commands.js`, `static/js/autocomplete.js`,
`static/js/chat.js`, `templates/index.html`, `tests/test_logoff.py` (new, 12 tests),
`tests/test_simple.py` (epoch restore), `CLAUDE.md`, `ADR/idle-session-lock.md`.
