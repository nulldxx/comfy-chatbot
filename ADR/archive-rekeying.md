# ADR: Password-derived LUKS keys (re-keying the encrypted volumes)

**Status:** Implemented.
**Scope decided before build:** re-key **both** volumes (archive + output) and include
a **recovery keyslot**.

## Problem

Both encrypted volumes — the **archive** (`ARCHIVE_VOLUME`) and the persistently-mounted
**output** (`OUTPUT_VOLUME` at `IMAGES_DIR`) — were unlocked with `SECRET_KEY`, which
sits in plaintext in the deployment's compose file. Anyone who read that file held the
LUKS passphrase and could decrypt the volumes offline.

## Decision

Make the effective LUKS passphrase depend on the user-changeable **login password**
(already stored only as an unrecoverable scrypt hash, see the change-password feature).
A leaked compose file (`SECRET_KEY`) alone — even with the login-hash file — can no
longer derive the volume key. This is a **keyslot change, not re-encryption**: instant,
preserves all data, but data-loss-capable, so it shipped as its own change.

## How it was implemented

### 1. The effective passphrase — `crypto_key.py` (new, stdlib-only)
- `derive_passphrase(secret_key, password)` = `sha256(secret_key || 0x00 || password)`
  hex. **Pinned** — changing it would orphan every keyslot.
- `effective_passphrase(secret_key, password_is_set, password)`: returns `secret_key`
  when no UI password is set (bootstrap — exactly the old behaviour); the derived value
  once one is; raises `VolumeLockedError` when a password is set but none is in memory.
- `app.effective_passphrase()` wraps it with `auth_store` state; used at every volume
  open site so the two ends never disagree.

### 2. In-memory login password — `auth_store.py`
- `set_session_password` / `current_password` / `clear_session_password`: a
  process-global (one gunicorn worker, shared threads) holding the plaintext login
  password after a successful login. **Never persisted**; gone on restart until the
  next login — the whole point of the lazy mount below.
- `generate_recovery_passphrase()` — `secrets.token_urlsafe(32)` (~256 bits).

### 3. New agent actions — `packaging/agent/archive-agent`
Keyslot-only ops that edit just the LUKS header (a few KB), so they work while a volume
is **mounted** and never touch the master key or data:
- `add-key {volume, password:<old>, new_password:<new>}` → `luksAddKey` then verify the
  new key opens (`--test-passphrase`). Idempotent: a no-op success if `<new>` already
  opens (a retry doesn't burn a keyslot).
- `remove-key {volume, password:<remove>, keep_password:<keep>}` → refuses unless
  `<keep>` opens first (never leave a volume with no working key) and refuses
  `remove == keep`; idempotent if `<remove>` is already gone.
- `header-backup {volume}` → `luksHeaderBackup` into `HEADER_BACKUP_DIR` (0700 dir,
  0600 files, unique timestamped name) as the rollback for the one irreversible step.
- `cryptsetup` is now declared explicitly in the deb `Depends`.

### 4. Re-key on password change — `app._rekey_and_commit` (`/api/change-password`)
Under `archive_lock` (never races an archive/host-mount/fsck op), with an explicit
commit point:

1. `header-backup` every **existing** target volume.
2. `add-key` old→new on every target (add-key verifies the new key opens).
3. First migration only: `add-key` old→**recovery** on every target.
4. **COMMIT** — `save_password_hash(new)` + `set_session_password(new)`.
5. `remove-key` old from every target (post-commit cleanup, best-effort).

**Invariant:** the login password (step 4) is persisted only after the new LUKS key is
proven to open every volume, so *password changed ⟺ new key unlocks the data*. A failure
before step 4 commits nothing (old password still works; a half-added keyslot is a
harmless extra a retry reuses). A failure during step 5 causes no lockout and no data
loss — only a stale old keyslot lingers. The header backup covers the sole irreversible
step (`luksAddKey`). The "old" key per volume is the bootstrap key on the first
migration (`SECRET_KEY`, or `OUTPUT_PASSWORD` for output) and the derived-from-current
key thereafter. Volumes whose backing file doesn't exist yet are skipped — they are
later created directly with `effective_passphrase()`, so they never need migration.

**Correction (existence must be probed via the agent).** "Which volumes exist" was
originally decided with `Path(v).exists()` *inside the container*. But `ARCHIVE_VOLUME`
/`OUTPUT_VOLUME` are **host paths mounted by the agent, not into the container**, so that
stat was always `False`: the re-key found "no volumes", skipped every one, yet still
committed the new password. Net effect on the live box — the password changed, the hash
was saved, but **neither volume was ever re-keyed** (both stayed on the bootstrap
`SECRET_KEY`), so `m`/host-mount failed to open the archive with the derived key and the
security goal was silently not met. Fixed by adding an agent `exists` action and a
`_volume_exists()` probe; the re-key now builds its target list from the agent's answer
and **fails closed** (aborts before the commit) if the agent can't report existence, so
an existing volume can never be left un-rekeyed. A second latent bug surfaced by this:
the agent's keyslot ops shell out to `cryptsetup`, which was absent on the host (the
`.deb` declares the dependency, but the deployed agent predated it) — install
`cryptsetup` on the host. Regression coverage lives in `TestExistenceViaAgent`
(`tests/test_rekey.py`) and `TestAgentExists` (`tests/test_agent_rekey.py`); the earlier
tests masked the bug by creating real local backing files so the in-container stat passed.

### 5. Effective passphrase at every open site — `app.py`
`api_archive`, `api_fscheck`, `api_host_mount` now pass `effective_passphrase()` instead
of the literal `SECRET_KEY`.

### 6. Lazy output-volume mount (the accepted behavioural change)
Once a password is set the output volume can no longer auto-mount at startup (no
password then):
- **`agent_client`** (`_mount_output` / `_check_output`, run by the entrypoint): skip
  when `auth_store.password_is_set()` — "deferred to first login".
- **`app._lazy_output_check_and_mount`**: on first login, fsck the output volume while
  still unmounted (writing the result for `/api/fscheck`), then mount it with the
  derived passphrase and wait for the marker. Runs on a daemon thread so login returns
  at once; image/session endpoints stay guarded by the existing `output_storage_error()`
  (marker-absent) check until the mount lands. Idempotent via `output_mount_lock`.
- **`login_required`**: when a password is set but the process holds none (a signed
  cookie survived a restart), force a fresh login so the password re-primes and the
  volume can unlock — realising the accepted "no access to images/sessions until login"
  posture. `/health` and `/login` are unaffected (login hash is on the unencrypted
  workflows mount).

### 7. Recovery keyslot (UI)
On the first password set the server returns the one-time recovery passphrase;
`commands.js` shows it once in the change-password success bubble with a copy button and
a "store this offline" warning. It is never stored server-side.

### 8. `m` host script (`~/dot-files/scripts/m`, separate repo)
`login()` was refactored around `_try_login`; when the compose `APP_PASSWORD` is
rejected (superseded by a UI password → HTTP 200 not 302) it prompts interactively (3
tries). A successful login both authenticates `m` and primes the app's in-memory
password so `/api/host-mount` can derive the LUKS key. Backward compatible: with no UI
password set, the compose password still logs in and `m` never prompts.

## Consequences / known limitations
- **Forgotten password ⇒ archive lost** unless the recovery key was saved. Recovery
  covers the volumes that existed at first migration; a volume created *after* migration
  is born with the derived key but **no** recovery keyslot (the recovery passphrase is
  never stored, so it can't be re-added).
- After every restart/redeploy the app has no access to images/sessions until someone
  logs in (single-user appliance — acceptable).
- Header backups are sensitive (backup + a known passphrase decrypts the volume) — kept
  0600 on a non-exported host dir.
- Transport is still cleartext HTTP on the trusted LAN; `SECRET_KEY` remains the Flask
  session key.

## Tests
- `tests/test_crypto_key.py` — pinned derivation + `effective_passphrase` states.
- `tests/test_rekey.py` — `/api/change-password` ordering (add/verify before hash save,
  remove after), recovery on first migration only, `OUTPUT_PASSWORD` as output's old
  key, mid-sequence failure leaves the old password working, host-mounted → 409.
- `tests/test_lazy_output_mount.py` — entrypoint defers when a password is set; lazy
  mount fscks-then-mounts with the derived passphrase; idempotent; login primes the
  password and the stale-cookie-after-restart path forces re-login.
- `tests/test_agent_rekey.py` — integration on a throwaway loopback LUKS file
  (add→remove→idempotence, refuse-last-key, refuse-same-key, wrong-old-password fails,
  restorable 0600 header backup). Guarded to **root + cryptsetup**, so it skips in CI.

## Rollout (on moria)
Full backup → deploy the updated `archive-agent` (additive actions; `HEADER_BACKUP_DIR`
has a safe default) and the app image → set a password (performs the first migration and
shows the recovery key) → verify the old `SECRET_KEY` no longer opens either volume and
the recovery key does. Ship the `m` change in dot-files.
