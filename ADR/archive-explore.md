# ADR: Browse the encrypted archive from the chat (`/archive-explore`)

**Status:** Implemented.
**Scope decided before build:** the volume is held open on a **lease** (auto-closed when
idle) rather than mounted per request; the browser is **read + delete**, not read-only;
the Slideshow button is **recursive** from the folder on screen.

## Problem

Archiving is a one-way door. `/archive-session`, `/archive-today` and `/archive-all`
copy media onto the LUKS-encrypted archive volume under `staging/<folder>/` and then
**delete the originals** (`api_archive`). After that the app has no read path back:
before this change, no endpoint listed the archive's contents or served a file from it.

The only way to see archived work again was `m` on the host — samba over a host bind,
which takes the one physical volume exclusively and makes `/api/archive` and
`/api/fscheck` refuse (`_HOST_MOUNT_BUSY`) for as long as it is up. That is a heavyweight
thing to do to glance at last month's renders, and it happens outside the app entirely.

## Decision

Add `/archive-explore`: a folder browser rendered in place in a chat bubble, with a
**▶ Slideshow** button that hands the current folder's media — recursively — to the
existing `createSlideshow()` reel, so archived stills and clips get the same 3s
auto-advance, video-duration-aware progress bar, arrow keys, swipe and fullscreen as
`/slideshow-session`.

### The lease is the whole design problem

Every other archive operation is one mount → work → unmount cycle inside a single
request. Browsing cannot be: a listing, every thumbnail, and every slide of a slideshow
is a separate HTTP request, spread over minutes. Three options were considered:

- **Mount per request** — a `zuluCrypt-cli` open/close per thumbnail. Glacial, and it
  cannot serve a video at all.
- **Hold `archive_lock` for the session** — rejected outright. `_lock_down()` acquires
  that lock *non-blocking* and gives up if it is busy, so a browse session holding it
  would defer the idle lockdown indefinitely. That lock exists precisely so a lockdown
  can happen.
- **A lease** (chosen) — the first request mounts, every subsequent one renews, and a
  watchdog closes the volume once nobody has browsed for
  `ARCHIVE_BROWSE_TIMEOUT_SECONDS` (default 600; `0` disables). The lock is taken only
  for the short mount/unmount calls.

### Refuse while the host has it

While `m` holds the volume, `/archive-explore` refuses with the existing
`run `m -u`` message, exactly as archiving and fsck do. Not merely for consistency: the
lease's auto-close issues `unmount`, which pops the bind samba is serving from.

## How it was implemented

### Backend

- **`archive_browse.py`** — the lease, modelled directly on `idle_lock.py` and sharing
  its reasoning: a **watchdog thread, not a re-armed `threading.Timer`** (a slideshow
  renews the lease every few seconds, so re-arming would spawn a thread per image);
  **started lazily, never at import** (gunicorn sets `preload_app = True`, and threads do
  not survive `fork()`); and **no import of `app`**, which wires itself in through
  `configure()`, so the module is unit-testable standalone. `check_now(now=…)` is split
  out of the loop so tests drive it with an injected clock instead of sleeping.
- **`config.py`** — `ARCHIVE_BROWSE_TIMEOUT_SECONDS`.
- **`app.py`** — `_archive_browse_open()` (mount + marker check + `touch()`),
  `_archive_browse_close()`, `_archive_browse_expired()` (the watchdog callback),
  `_archive_path()` (containment), and six routes:
  `GET/DELETE /api/archive-browse`, `GET /api/archive-browse/media`,
  `POST /api/archive-browse/close`, `GET /api/archive-browse/status`, and
  `GET /archive-file/<path:rel>`.

Three details worth recording:

- **`_archive_browse_open()` deliberately omits `create`.** `api_archive` self-provisions
  the volume on first archive because it is about to write to it. Browsing must never
  conjure an empty 20G volume just because somebody typed the command.
- **The marker check is reused verbatim.** Without `(ARCHIVE_MOUNT_DIR /
  ARCHIVE_MARKER).exists()` a listing would read the container's own writable layer and
  report an empty archive as though the files were gone — a uniquely alarming lie for
  this feature to tell.
- **Every existing unmount site now calls `archive_browse.note_closed()`** —
  `api_archive`'s `finally`, the fsck job, `api_host_mount`, and `_lock_down()`. The
  volume can be closed out from under a browse session by any of them; the lease must
  not then believe it still holds a mount, or its watchdog would unmount a second time
  and could pop a host bind established in the meantime. The client simply re-opens on
  its next request, so this is invisible in use.

`/api/archive-browse/media` sorts by **relative path**, not mtime, so subfolders stay
grouped and the `<folder>001, <folder>002 …` names `api_archive` assigns keep the order
the user drag-sorted before archiving.

### Frontend

- **`static/js/archive.js`** — `renderArchiveBrowser(bubble)`. Breadcrumb + toolbar
  (Slideshow / Up / Close archive) + `.sel-list` folder rows + a file grid that reuses
  the `/review` grid's classes (`.review-frame`, `.review-grid`, `.review-thumb`,
  `.review-del`) wholesale. Navigating re-renders **in place** via `clearBubble()`, which
  preserves the bubble's ✕, so the panel is one bubble for its whole life.
- **`static/js/slideshow.js`** — the reel already took a plain array of URL strings and
  worked verbatim on `/archive-file/…` URLs (`isVideoUrl` matches on extension, not
  prefix). The single gallery-specific thing was `deleteCurrent()` posting to
  `/api/images/<basename>`. It now takes an injected `deleteMedia`, **defaulted to
  `deleteImageFile`** so no existing caller changed.
- **`static/js/dom.js`** — `deleteArchiveFile(url)` beside `deleteImageFile(url)`. The
  whole relative path is the identity in a tree, so it goes in a query parameter rather
  than the URL path.
- **`static/js/lightbox.js`** — a **guard, not a seam**. The lightbox is reachable from
  an archive slideshow and its delete also posts to `/api/images`; rather than thread a
  deleter through every `openLightbox` call site, it bails on any URL not under
  `/images/`. Archive deletion lives on the thumbnail 🗑 and the slideshow's 🗑.
- **`static/js/utils.js`** — `archiveBreadcrumb`, `archiveParentPath`, `joinArchivePath`.
  These are pure and live here because `tests/js` runs Jest with
  `testEnvironment: "node"` and no jsdom: by this codebase's precedent no DOM-rendering
  module is unit-tested, so the testable logic is factored out to where it can be.

## Consequences

- **Signing out, an idle lockdown, an archive op or an fsck all close the volume under an
  open browser.** This is correct — each of them must — and it is self-healing: the next
  click re-mounts transparently. A slideshow playing at that moment stops.
- **Deletes are permanent.** The archive is the last copy, since archiving removes the
  gallery original. The browser says so in `/help` and names each file as it goes.
- **A file can be truncated mid-download.** `send_from_directory` streams *after* the
  handler returns and releases `archive_lock`, so an archive op or fsck starting mid-
  stream can unmount beneath an in-flight video. In practice the busy filesystem makes
  that unmount fail (logged, and self-healing on the next op), and the cost is at worst
  one broken video that reloads fine — cheaper than buffering whole videos in memory or
  holding the lock for the length of a download.
- **The volume now spends more time decrypted than before** — bounded by
  `ARCHIVE_BROWSE_TIMEOUT_SECONDS` and by the idle lockdown, which still closes
  everything. `Close archive` is the button for wanting it shut now; `/fscheck` needs it
  shut, which makes that button's effect easy to verify.
- **Not covered:** no restore-to-gallery (archived media cannot be fed back into i2v or
  face-detail without going through `m`), no folder deletion, no rename or move, and no
  seed data — `seed_store` is keyed on gallery filenames and archiving drops the entry.

## Tests

- **`tests/test_archive.py::TestArchiveExplore`** — 20 cases reusing the existing
  `FakeAgent` Unix-socket harness, so the mount/unmount handshake and the marker it drops
  are exercised for real and only zuluCrypt is faked. Covers listing one level and
  nested; the recursive folder count; the marker and `lost+found` staying hidden;
  traversal (`../`, absolute paths, the marker) rejected; the no-marker refusal; the 409
  while host-mounted (asserting no mount was even attempted); that `create` is never
  sent; recursive media ordering; serving; delete removing a file and refusing a folder
  and the root; `close` issuing `unmount`; that `status` never mounts; and that an
  archive op drops the lease.
- **`tests/test_archive_browse.py`** — 13 cases driving the lease with an injected clock:
  it stays disarmed until touched, does not fire early, fires once and only once, is
  renewed by a browse request, is disabled by a `0` timeout, is disarmed by
  `note_closed()` and re-armed by `touch()`, and survives a callback that returns falsy
  (retried next tick) or raises. Plus the app-side wiring: the callback is the app's, it
  is a no-op with no volume configured, and it defers rather than blocks while
  `archive_lock` is held.
- **`tests/js/utils.test.js`** — the three path helpers, including every traversal form
  `joinArchivePath` must refuse.
