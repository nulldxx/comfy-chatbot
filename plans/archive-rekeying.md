# Plan: Password-derived LUKS keys (re-keying the encrypted volumes)

## Context

Today both encrypted volumes — the **archive** (`ARCHIVE_VOLUME`) and the **output**
(`OUTPUT_VOLUME`, mounted at `IMAGES_DIR`) — are unlocked with `SECRET_KEY`, which sits
in **plaintext** in `~/dot-files/docker-compose/comfy-chatbot.yml`. Anyone who reads that
compose file therefore holds the LUKS passphrase and can decrypt the volumes offline.
(`app.py` passes `"password": SECRET_KEY` to the agent for archive mount/host-mount/fsck;
`agent_client.py` uses `OUTPUT_PASSWORD or SECRET_KEY` for the output volume.)

We already added a user-changeable **login** password (`auth_store.py`, scrypt hash at
`/app/workflows/.auth.json`, superseding `APP_PASSWORD` once set). This plan extends that:
**make the LUKS passphrase depend on the login password** so the compose `SECRET_KEY` alone
no longer decrypts anything. Because the password is stored only as an unrecoverable scrypt
hash, an attacker with the compose file *and* the hash file still cannot derive the volume key.

This is a **keyslot change, not a full re-encryption** — instant, preserves all data — but it
is a data-loss-capable operation on the volumes that hold the user's archive, so it ships as
its own deliberate change (separate from the login-password PR, which is already merged).

## Threat model

| Threat | Before | After |
|---|---|---|
| Compose file (`SECRET_KEY`) leaks | Archives decryptable | **Safe** — key also needs the password (never stored in recoverable form) |
| Volume file stolen at rest (no password set) | Decryptable with compose key | Unchanged (bootstrap still uses `SECRET_KEY`) |
| Volume file stolen at rest (password set) | — | **Safe** — needs `SECRET_KEY` **and** the password |
| Login-hash file (`.auth.json`) also stolen | — | Still safe — scrypt hash is not reversible to the password |
| Password forgotten | File delete resets login | **Archives unrecoverable** unless a recovery keyslot exists (see below) |

Out of scope (unchanged): transport is still cleartext HTTP on the trusted LAN;
`SECRET_KEY` remains the Flask session key.

## Core concept: the effective passphrase

A single shared helper derives the LUKS passphrase, used identically everywhere a volume is
opened so the two ends never disagree:

```
effective_passphrase(secret_key, password):
    if not password_is_set():        # bootstrap / no UI password yet
        return secret_key            # exactly today's behaviour
    return sha256(secret_key + "\x00" + password).hexdigest()
```

- Put it in a new tiny module importable by both `app.py` and `agent_client.py` (e.g.
  `crypto_key.py`, stdlib-only so it runs in the slim runtime image and the entrypoint).
- `password` here is the **plaintext** login password. The app only has it transiently — at
  login (form submit) and at change-password. It is **held in memory** for the process
  lifetime after a successful login and **never persisted**. On restart it is gone until the
  next login (this is the whole point — see "Lazy mount").
- Hashing (rather than raw `secret + password` concatenation) avoids any ambiguity/length
  edge cases and produces a fixed-width passphrase. **Once chosen, this derivation can never
  change** without re-keying, so pin it and unit-test it.

## Component changes

### 1. In-memory password (`app.py` + `auth_store.py`)
- Add a process-global holder (e.g. `auth_store.set_session_password(pw)` / `current_password()`),
  populated in `/login` on a successful `verify_password`, and updated in `/api/change-password`
  after a successful save. Never written to disk.
- `effective_passphrase()` reads `current_password()`; if a password is set but none is in
  memory (app restarted, nobody has logged in yet), any volume op must fail cleanly with
  "log in first" rather than falling back to `SECRET_KEY` (which would no longer work anyway).

### 2. Lazy output-volume mount (the accepted behavioural change)
Once a password is set, the output volume can no longer auto-mount at startup (no password is
available then). It becomes **mount-on-first-login**:
- **`docker-entrypoint.sh` / `agent_client.py`**: `check-output` and `mount-output` become
  conditional — if `auth_store.password_is_set()` (the `.auth.json` file exists on the
  workflows mount, readable at startup), **skip** them and log "output mount deferred to login".
  Otherwise behave exactly as today (mount with `SECRET_KEY`).
- **`app.py`**: after the first successful login (when a password is set), call the agent's
  `mount` for `target=output` with `effective_passphrase()`, wait for `OUTPUT_MARKER`, and only
  then treat `IMAGES_DIR` as available. Guard image/session endpoints so that, pre-mount, they
  return a clear "not unlocked yet — log in" error rather than writing to bare disk.
  - Consequence, already accepted: after every restart/redeploy the app has **no access to
    images/sessions until someone logs in**. `/health` and `/login` still work (login hash is
    on the unencrypted workflows mount).
  - `check-output` (fsck) similarly moves to run once, post-login, before the lazy mount.

> **Reduced-scope alternative (lower risk):** re-key **only the archive volume** and leave the
> output volume on `SECRET_KEY`. This keeps startup auto-mount and avoids the "app dead until
> login" behaviour entirely, at the cost of leaving live output images decryptable via a compose
> leak. If chosen, sections 2 and the output parts of 3/5 drop out and only `/api/archive`,
> `/api/host-mount`, `/api/fscheck` and `m` are touched. **Recommend deciding this before build.**

### 3. New agent actions: `add-key` / `remove-key` (`packaging/agent/archive-agent`)
Re-key via keyslot add-then-remove (safer and idempotent vs. `luksChangeKey`). Reuse the
existing `_write_keyfile` / tmpfs-keyfile pattern (keeps passphrases off `argv`). New handlers:

- `{"action":"add-key", "volume":..., "password":<old>, "new_password":<new>}`
  → `cryptsetup luksAddKey --key-file <oldkf> <volume> <newkf>`, then verify with
  `cryptsetup open --test-passphrase --key-file <newkf> <volume>`. Idempotent-ish: if `<new>`
  already opens, treat as success.
- `{"action":"remove-key", "volume":..., "password":<remove>, "keep_password":<keep>}`
  → require `<keep>` opens (`--test-passphrase`) as a safety check, then
  `cryptsetup luksRemoveKey --key-file <removekf> <volume>`. Refuse if `<keep>` and `<remove>`
  are the same, or if removing `<remove>` would leave the volume with no other working slot.
- `{"action":"header-backup", "volume":...}` → `cryptsetup luksHeaderBackup <volume>
  --header-backup-file <path under a safe host dir>` before the first re-key (see §6).
- These work while the volume is **mounted** (keyslot ops touch only the LUKS header), so no
  unmount/remount is needed. Wire them into `handle()` alongside the existing actions. Add
  matching docstring lines to the agent header comment.

### 4. Re-key on password change (`app.py` `/api/change-password`)
Extend the existing endpoint. After `verify_password(current)` passes and **before/around**
`save_password_hash(new)`:
1. Compute `old_pp = effective_passphrase()` (current in-memory password, or `SECRET_KEY` if no
   password set yet — the first-time migration case) and `new_pp = derive(SECRET_KEY, new)`.
2. For **each** volume in scope (archive [+ output], under `archive_lock`):
   - `header-backup` (first migration only, or always — cheap).
   - `add-key old→new`.
3. Verify `new_pp` opens **both** volumes (`--test-passphrase`).
4. Only then `save_password_hash(new)` and update the in-memory password.
5. `remove-key old` from **both** volumes.
- **Ordering matters for safety:** add-new to both → verify both → persist hash → remove-old
  from both. A crash after step 2/3 leaves *both* passphrases valid on both volumes (safe,
  re-runnable). The only bricking risk is a header corruption during `luksAddKey`, which the
  header backup covers.
- Serialise the whole sequence under `archive_lock` so it never races an archive op, host-mount
  or fsck. Return a structured error (and leave the old password working) if any volume step
  fails, so the user can retry.

#### Transactionality — commit point & guarantees
There is **no bulk data transfer / re-encryption** to fail: keyslot ops rewrite only the LUKS
header (a few KB), so the master key and all archive data are untouched. There is no cross-volume
+ cross-file two-phase-commit primitive, so instead the sequence has an explicit **commit point**:

```
1. header-backup both volumes
2. add-key old→new on BOTH
3. verify new opens BOTH (--test-passphrase)
4. COMMIT: save_password_hash(new) + set in-memory password   <-- the password change
5. remove-key old from BOTH   (post-commit cleanup)
```

Guarantees this gives (state the invariant in the endpoint's docstring):
- **The login password (step 4) is written only after the new LUKS key is proven to open both
  volumes.** So "password changed" ⟺ "new key unlocks the data" — you can never end up with a
  changed password that doesn't unlock the volumes, nor locked out of the data.
- **Fail before step 4** (add/verify): nothing commits — old password still logs in and still
  opens both volumes; any half-added new keyslot is a harmless extra that a retry reuses.
- **Fail during step 5** (removing the old key): no lockout and no data loss — the new password
  works everywhere; the only residual is that the old `SECRET_KEY` slot may still open a volume
  until cleanup reruns (a hardening gap, not a correctness failure).
- **Self-healing removal:** on login/startup, when a password is set, test whether the old
  `SECRET_KEY` (or any prior key) still opens each volume via `--test-passphrase`; if so,
  re-attempt `remove-key`. This closes the step-5 window automatically without user action.
- The only step that is not itself reversible-by-inaction is `luksAddKey` (it edits the header);
  the header backup taken in step 1 is the rollback for a corrupted header.
- **Output volume caveat:** it is mounted during the re-key — fine for keyslot ops. If a password
  is being set for the *first time* while the output volume was auto-mounted at startup with
  `SECRET_KEY`, that's exactly the migration path (old_pp = `SECRET_KEY`).

### 5. Use the effective passphrase at every open site (`app.py`)
Replace the literal `"password": SECRET_KEY` at the three call sites with
`effective_passphrase()`:
- `api_archive()` (~`app.py:1686`) — archive mount.
- `api_fscheck()` (~`app.py:1792`) — archive fsck.
- `api_host_mount()` (~`app.py:1821`) — host bind mount (the `m` path).
And in `agent_client.py`, the output `mount`/`fsck` passphrase becomes the effective one (only
reached post-login once a password is set; pre-set it stays `OUTPUT_PASSWORD or SECRET_KEY`).

### 6. Recovery keyslot (strongly recommended)
Once LUKS gates on the password, **a forgotten password means the archive is gone.** Mitigate:
- On the **first** password set, generate a random high-entropy recovery passphrase, add it to a
  spare keyslot on both volumes (`add-key`, authenticated by the current key), and **display it
  once** to the user in the `/change-password` success UI with "store this offline — it is the
  only way to recover the archive if you forget your password." Never store it server-side.
- Document `cryptsetup luksOpen` with the recovery key as the manual recovery path. Optionally a
  future `/recover` flow that accepts the recovery key and re-keys to a new password.

### 7. `~/dot-files/scripts/m` — prompt for the password when the compose one is rejected
`m` logs into the web app with `APP_USERNAME`/`APP_PASSWORD` read from the compose file, gets a
session cookie, then calls `/api/host-mount`. Two things change once a UI password is set:
- The compose `APP_PASSWORD` is **superseded**, so `m`'s headless login now returns HTTP 200
  (rejected) instead of a 302.
- `/api/host-mount` needs the app to hold the plaintext password in memory to derive the LUKS
  key — and a successful login is exactly what primes it. So making `m` log in with the real
  password both authenticates it *and* unlocks host-mount.

Change `m`'s `login()` to fall back to an interactive prompt:
```bash
login() {
    local pw="$PASSWORD" status
    status="$(_try_login "$pw")"          # POST /login, echo HTTP status
    if [[ "$status" == 30* ]]; then return 0; fi   # compose password still works
    # Superseded by a UI-set password (200 = form re-rendered). Prompt for it.
    echo "m: the archive password has been changed from the compose value." >&2
    for attempt in 1 2 3; do
        read -rsp "m: enter archive password: " pw; echo >&2
        status="$(_try_login "$pw")"
        [[ "$status" == 30* ]] && return 0
        echo "m: incorrect password ($((3-attempt)) tries left)" >&2
    done
    echo "m: authentication failed" >&2; exit 1
}
```
- Factor the existing `curl … /login` into `_try_login <pw>` that echoes `%{http_code}` and sets
  the cookie jar `-c "$JAR"`.
- Everything after login is unchanged: `/api/host-mount` (mount) and `/api/host-unmount` (`m -u`)
  work as before, now that the app holds the in-memory password from this login.
- Backward compatible: if no UI password has been set, the compose `APP_PASSWORD` still logs in
  and `m` never prompts. `m` lives in the **dot-files** repo — ship it there.

## Migration / first-time flow (what actually happens the first time a password is set)
1. Volumes are currently keyed on `SECRET_KEY`; app running, output volume mounted with `SECRET_KEY`.
2. User runs `/change-password` (current = the effective `APP_PASSWORD`, since no UI password yet).
3. Endpoint: header-backup both → `add-key` SECRET_KEY→new_pp on both → verify new_pp opens both →
   generate + add recovery key → save scrypt hash + set in-memory password → `remove-key` SECRET_KEY
   from both.
4. From now on: on restart the output volume no longer auto-mounts; the app waits for a login,
   then mounts it with `effective_passphrase()`.

## Data-loss risks & safeguards
- **Header backup before every re-key** (§3/§6); keep backups on a non-encrypted host dir with
  0600 perms — note a header backup + a known passphrase can decrypt the volume, so treat it as
  sensitive.
- **Full archive backup before the first migration** — recommend the user run one; a keyslot op is
  low-risk but the data is irreplaceable.
- **Add-then-verify-then-remove** ordering so no window leaves a volume with only an unverified key.
- **Never remove the last keyslot** — the `remove-key` handler must refuse if `keep_password`
  doesn't open the volume first.
- **Recovery keyslot** as the backstop for a forgotten password.

## Testing / verification
1. **Unit:** `crypto_key.effective_passphrase` — `SECRET_KEY` when no password set; deterministic
   derived value when set; changes with the password; never equals `SECRET_KEY` once set.
2. **Agent (integration, on a throwaway loopback LUKS file, not the real volumes):**
   create a small volume keyed with pw A → `add-key` A→B → both A and B open → `remove-key` A
   (keep B) → only B opens → `remove-key` refuses to drop the last key → `header-backup` produces
   a restorable header. Script under `test-docker/` or a dedicated integration test guarded to
   only run against a temp file.
3. **App (test client):** `/api/change-password` triggers the agent `add-key`/`remove-key`
   sequence (mock `_agent_request`); asserts ordering (add+verify before hash save; remove after),
   and that a mocked mid-sequence failure leaves the old password working and returns an error.
4. **Lazy mount:** with a password set, entrypoint skips `mount-output`; a pre-login image/session
   request returns "locked"; first login mounts and unlocks. (Test the guard logic with mocks.)
5. **`m` (manual on moria):** with no UI password → `m` mounts headlessly as today. After setting a
   UI password → `m` prompts, a wrong password retries then fails, the correct password mounts;
   `m -u` unmounts. Confirm `/fscheck` and `/api/archive` still refuse while host-mounted (409).
6. **End-to-end on moria (after a full backup):** set a password, restart the container, confirm
   images are inaccessible until login, then accessible after; confirm the archive opens with both
   the password and the recovery key; confirm the old `SECRET_KEY` no longer opens either volume
   (`cryptsetup open --test-passphrase`).

## Rollout order
1. Land `crypto_key.py` + unit tests (no behaviour change yet — `effective_passphrase` returns
   `SECRET_KEY` until a password is set).
2. Land agent `add-key`/`remove-key`/`header-backup` + integration tests (host `archive-agent`
   update required on moria; new actions are additive).
3. Land app re-key-on-change + effective-passphrase call sites + lazy output mount + entrypoint
   guard, behind the existing "password set?" condition so nothing changes until a password is set.
4. Ship the `m` change in dot-files.
5. On moria: full backup → deploy → set a password (performs the first migration) → verify §6.

## Open decisions to confirm before building
- **Scope:** re-key **both** volumes (recommended, honours the earlier decision — accepts the
  lazy-mount posture) vs. **archive-only** (lower risk, keeps startup auto-mount, but leaves live
  output images compose-decryptable).
- **Recovery keyslot:** include the generate-and-show-once recovery passphrase (recommended) or
  rely solely on the user never forgetting the password.
