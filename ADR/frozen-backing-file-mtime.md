# ADR: Freeze the encrypted volumes' backing-file mtime

**Status:** Implemented.
**Scope decided before build:** freeze **literally forever** — pinned to a fixed baseline
even after genuinely archiving new images (not merely "housekeeping-neutral").

## Problem

The encrypted archive/output volumes are single LUKS backing files on the host (a fixed
~20 GB file). Every routine agent operation rewrites bytes *through* the loop/dm-crypt
device into that file and bumps its **mtime**, even when no image data changed:

| Op | Why it bumps the backing file's mtime |
|---|---|
| `mount` / `host-mount` | rw ext4 superblock + per-mount `.comfy-archive` marker + `chmod`/`chmod -R` |
| `unmount` / `host-unmount` | dirty-page flush + clean-superblock write on close |
| `fsck` (`/fscheck`, startup check) | `e2fsck -f -y` writes the superblock last-check time **every run** |
| `add-key` / `remove-key` (password change) | LUKS header keyslot edit |

The owner wanted the encrypted archives to **never** have their modified time altered.

## Decision

Pin each volume's backing-file mtime to an immutable per-volume baseline, restored by the
root **`archive-agent`** after every op that leaves the volume closed. The agent is the
only component with host-filesystem access to these paths (the container is unprivileged
and cannot even `stat` them), so the fix lives entirely there — no app, client, or
protocol changes.

Accepted trade-off: the LUKS file is a **fixed size**, so with mtime frozen a size+mtime
backup (plain `rsync`) will never re-copy it — content-aware backups (`rsync -c`,
`restic`, `borg`) are required if the archive is to be backed up.

## How it was implemented — all in `packaging/agent/archive-agent`

### 1. Config (`DEFAULTS`)
- `MTIME_BASELINE_DIR` = `/var/lib/archive-agent/mtime-baselines` (0700, beside
  `HEADER_BACKUP_DIR`).
- `PRESERVE_MTIME` = `"1"` — truthy toggle; `_preserve_mtime(cfg)` treats
  `0/false/no/off/""` as off.

### 2. Helpers
- `_baseline_path(cfg, volume)` — flatten the absolute volume path to one filename under
  `MTIME_BASELINE_DIR`.
- `_load_or_init_baseline(cfg, volume)` — return the stored `mtime_ns`; if the sidecar is
  absent, **bootstrap** it once from the file's current `st_mtime_ns` (written 0600 via
  `os.open(..., O_WRONLY|O_CREAT|O_TRUNC, 0o600)`), then never change it. Returns `None`
  when disabled or the volume file is absent.
- `_freeze_mtime(cfg, volume)` — `os.utime(volume, ns=(baseline, baseline))`.
  **Best-effort**: logs and swallows `OSError`, never fails the caller's op.

### 3. Baseline born at creation
`_create_volume` gained a `cfg` parameter and calls `_load_or_init_baseline` after the
successful `mkfs`/close, so a freshly provisioned volume freezes at its creation time.

### 4. Restore after every closing op
`_freeze_mtime(cfg, volume)` is called once the volume is closed in:
- `handle_unmount` (after `zuluCrypt-cli -q -d`) — also covers `handle_host_unmount`
  (delegates) and the `api_archive` mount→copy→**unmount** path, so archiving new images
  no longer advances mtime.
- `handle_fsck` (`finally`, after `_cryptsetup_close`).
- `handle_add_key` / `handle_remove_key` (after the keyslot edit + verification).

`handle_mount` / `handle_host_mount` are untouched — they intentionally leave the volume
mounted; the paired unmount (or the next closing op for a rekey) restores the baseline.

## Guarantees and the one limitation

- **Archive volume** (unmounted at rest): fully frozen at every observable at-rest moment.
- **Output volume** (mounted continuously): frozen only when **closed** (container stop,
  rekey/lazy-mount cycle); its mtime drifts while live-mounted and cannot be pinned
  without freezing the running filesystem.
- Only the *outer* host file's mtime changes — never the *inner* ext4 superblock
  timestamps `e2fsck` uses — so fs integrity and cryptsetup/zuluCrypt open are unaffected.
- `ctime` still moves (a `utime` side effect); irrelevant to mtime+size backup tools.
- **Reset:** delete the volume's sidecar under `MTIME_BASELINE_DIR` → next op
  re-bootstraps from the current mtime.

## Tests

`tests/test_agent_rekey.py::TestMtimeFreeze` — no root/LUKS needed (the helpers only
stat/utime the outer file): restore-after-bump, once-only immutable bootstrap, frozen
value is the persisted baseline not "now", `PRESERVE_MTIME=0` no-op, absent-volume safety.
The existing loopback keyslot tests still exercise the wired `handle_*` ops under root.

## Deployment

Redeploy the `archive-agent` on the host (`moria`); the container image is unchanged. The
new `mtime-baselines` dir is created on demand, so `/etc/archive-agent.conf` needs no
change.
