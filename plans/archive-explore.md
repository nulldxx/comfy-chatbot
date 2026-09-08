# `/archive-explore` — an in-chat browser for the encrypted archive

## Context

Images and videos leave the gallery for good when they are archived. `/archive-session`,
`/archive-today` and `/archive-all` copy media onto the LUKS-encrypted **archive** volume
under `staging/<folder>/` and then delete the originals (`api_archive`, `app.py:2575`).
After that the only way to see them again is `m` on the host — samba, which takes the one
physical volume exclusively and blocks archiving and `/fscheck` while it is up. There is
no read path through the app itself: no endpoint lists archive contents or serves a file
from it.

`/archive-explore` adds one — a small folder browser rendered in place in the chat, with a
**▶ Slideshow** button that hands the current folder's media (recursively) to the existing
`createSlideshow()` reel, so archived stills and clips get the same controls, 3s auto-
advance, video-duration-aware progress bar, swipe and fullscreen as `/slideshow-session`.

Three decisions taken with the user up front:

- **Mount lifetime** — the archive is unmounted at rest, so browsing needs it held open
  across many requests. A **lease** (renewed per request, auto-closed when idle) plus an
  explicit **Close archive** button, rather than holding `archive_lock` for minutes.
- **Read + delete** — files can be deleted from the archive, from a browser thumbnail or
  the slideshow's existing 🗑 button.
- **Slideshow is recursive** from the folder on screen, so it also works at the volume
  root (where everything sits one level down under `staging/`).

## Design constraints this has to respect

1. **The volume is unmounted at rest.** Every existing op mounts inside one request and
   unmounts in a `finally`. Browsing spans minutes and many requests (each `<img src>` is
   one), so mount-per-request is untenable.
2. **`archive_lock` must not be held across requests.** `_lock_down()` (`app.py:2431`)
   acquires it *non-blocking* and gives up if busy — a browse session holding it would
   defer the idle lockdown indefinitely. The lease is therefore separate state; the lock
   is taken only for the short mount/unmount/list calls themselves.
3. **The marker check is the safety contract.** `api_archive` refuses to touch anything
   under `ARCHIVE_MOUNT_DIR` unless `(ARCHIVE_MOUNT_DIR / ARCHIVE_MARKER).exists()` —
   without it a read lands on the container's plain writable layer instead of the
   decrypted volume. Every browse read reuses that idiom.
4. **One physical volume.** While the host has it (`_host_mount_active()`, `app.py:2491`),
   the explorer must refuse exactly as `/api/archive` and `/api/fscheck` do — the lease's
   auto-close issues `unmount`, which pops the host bind and would break samba under `m`.

## Backend

### New module: `archive_browse.py`

A lease watchdog, modelled directly on `idle_lock.py` (same shape, same reasons) so it is
testable standalone and never imports `app`:

- `configure(timeout, on_expire)` — wired from `app.py`.
- `touch()` — renew the lease; calls `ensure_started()`.
- `ensure_started()` — create the daemon watchdog **lazily, never at import**: gunicorn
  sets `preload_app = True`, so a thread created pre-fork does not survive `fork()`. The
  same trap `idle_lock` documents.
- `note_closed()` — mark the volume already closed so the watchdog does not repeat the
  work (used by the Close button, and by every existing op that unmounts underneath us).
- `is_open()` — backs `/api/archive-browse/status`.

A watchdog thread rather than a re-armed `threading.Timer`: a slideshow renews the lease
on every media fetch, and re-arming would spawn a thread per image — the identical
reasoning `idle_lock.py` records.

`config.py`: add `ARCHIVE_BROWSE_TIMEOUT_SECONDS` (default `600`; `0` disables auto-close).

### New helpers in `app.py`

- `_archive_browse_open()` — under `archive_lock`: refuse if `_host_mount_active()`
  (`_HOST_MOUNT_BUSY`, 409); `_agent_request({"action": "mount", …})` with
  `effective_passphrase()` and **no** `create` (browsing must not conjure an empty volume
  — unlike `api_archive`, which self-provisions on first archive); assert the marker;
  `archive_browse.touch()`. Idempotent — the agent's `_open_volume` recovers when the
  volume is already open, so a repeat call just re-establishes the bind.
- `_archive_browse_close()` — under `archive_lock`: `unmount`, then `note_closed()`.
- `_archive_path(rel)` — resolve a client path against `ARCHIVE_MOUNT_DIR` with
  `werkzeug.security.safe_join` (`None` on traversal), rejecting any component starting
  with `.` (hides the `.comfy-archive` marker) and `lost+found`. Mirrors the containment
  idiom in `image_store.resolve_reference` (`image_store.py:100`).

Every existing site that unmounts the archive gains one `archive_browse.note_closed()`
line so the lease does not think it still holds a mount: `api_archive`'s `finally`,
`api_fscheck`'s job, `api_host_mount`, and `_lock_down()`. The client transparently
re-opens on its next request.

### New routes

All `@login_required`. None use `@requires_output_storage` — that guards the *output*
volume, and `api_archive` does not use it either. Each calls `_archive_browse_open()`
first (mount-if-needed) and renews the lease, so there is no separate "open" endpoint and
no ordering for the client to get wrong.

| Route | Returns |
|---|---|
| `GET /api/archive-browse?path=<rel>` | `{path, parent, dirs: [{name, path, count}], files: [{name, path, url, size, is_video}]}` — one level, each list sorted by name |
| `GET /api/archive-browse/media?path=<rel>` | `[url, …]` — every `MEDIA_EXTS` file at or below `<rel>`, **sorted by relative path** so subfolders stay grouped and `folder001…folder002` order holds. Same bare-array shape `/api/images` returns, which is what `createSlideshow` wants |
| `GET /archive-file/<path:rel>` | the file, via `send_from_directory(ARCHIVE_MOUNT_DIR, rel)` + `Cache-Control: no-store` — mirrors `serve_image` (`app.py:1948`) and `serve_reference` (`app.py:701`), but with a `<path:>` converter since the archive is nested |
| `DELETE /api/archive-browse?path=<rel>` | `{ok: true}` — unlink one file (files only, never a directory) |
| `POST /api/archive-browse/close` | `{ok: true}` — unmount now |
| `GET /api/archive-browse/status` | `{configured, open, host_mounted}` |

`503` when `ARCHIVE_VOLUME` is unset (matching `api_archive`), `409` when host-mounted,
`400` on a path failing containment, `404` on a missing entry.

### Known, accepted limitation

`send_from_directory` streams **after** the handler returns and releases `archive_lock`, so
an `/api/archive` or `/fscheck` starting mid-stream can unmount under an in-flight video.
In practice the busy filesystem makes that `unmount` fail, the existing code logs a
warning and self-heals on the next op, and the user sees at worst one truncated file. The
alternatives — buffering whole videos in memory, or holding `archive_lock` for the length
of a download — are worse. Record it in the ADR.

## Frontend

### New module: `static/js/archive.js`

`renderArchiveBrowser(bubble)`, following the `renderWorkflowsTable` (`commands.js:1226`)
and `renderSequenceReview` (`grids.js:10`) patterns — build DOM into a bubble the caller
created, reuse the existing `.sel-list` / `.sel-btn` / `.status-text` primitives
(`chat.css:136-147`, `:1008`), end with `scrollBottom()`:

- **Header** — a clickable breadcrumb (`archive / staging / man-on-beach`) built from the
  `‹ back` idiom at `commands.js:1259`, plus `▶ Slideshow` and `Close archive` buttons.
- **Folders** — text rows as `.sel-btn` (`📁 name — N items`), the cheapest thing that
  reads well and matches `/workflows`.
- **Files** — a thumbnail grid below the folders, shaped like `.review-grid`
  (`chat.css:763`, `grid-template-columns: repeat(auto-fill, minmax(110px, 1fr))`), each
  cell an `<img>`/`<video>` from `createMediaElement` (`dom.js:53`) with a `✕` overlay
  that calls `DELETE /api/archive-browse` and drops the cell. Clicking a still opens the
  lightbox with the folder's URLs as the collection, exactly as `renderReviewGrid` does.
- **Status line** — the `Loading…` / `⚠` idiom used throughout `commands.js`, and the
  house error style `r.json().then(data => ({ ok: r.ok, data }))`.

Navigating re-fetches and re-renders **in place** via `clearBubble(bubble)` (`dom.js:47`),
which preserves the `.msg-close` ✕, so the panel is one bubble for its whole life.

### Slideshow: one injectable seam

`createSlideshow(bubble, images)` already takes a plain array of URL strings and works
verbatim with `/archive-file/…` URLs — `isVideoUrl()` matches on extension, not prefix.
The one thing hard-wired to the gallery is `deleteCurrent()`, which posts to
`DELETE /api/images/<basename>` (`slideshow.js:147`).

Give it an injected deleter, defaulting to today's behaviour so **no existing caller
changes**:

```js
export function createSlideshow(bubble, images, { deleteMedia = deleteImageFile } = {})
```

`dom.js` already exports `deleteImageFile(url)` (`dom.js:69`); add `deleteArchiveFile(url)`
beside it. The archive browser passes the latter.

### Lightbox: a guard, not a seam

`deleteCurrentLightboxImage()` (`lightbox.js:90`) also posts to `/api/images` and is
reachable from a slideshow tap. Rather than thread a deleter through every `openLightbox`
call site, bail early when the URL is not under `/images/` and reuse its existing
`lbSetStatus` message. Archive deletion stays on the thumbnail ✕ and the slideshow's 🗑.

### Pure helpers in `utils.js` (this is where the JS test coverage goes)

`tests/js` runs Jest with `testEnvironment: "node"` and no jsdom, so by precedent no
DOM-rendering module is unit-tested. Put the logic that *can* be tested in `utils.js`
alongside `splitWorkflowVariant` and friends, and test it in `tests/js/utils.test.js`:

- `archiveBreadcrumb(path)` → `[{name, path}, …]` segments, root first.
- `archiveParentPath(path)` → the path one level up, `''` at the root.
- `joinArchivePath(base, name)` → normalised child path, rejecting `..` and absolute
  segments client-side too.

### Registration

- `commands.js` — one `if (cmd === '/archive-explore')` branch placed **after** the single
  `addMessage('user', …)` echo at `commands.js:2302`, so it must *not* echo the user line
  itself; it just calls `renderArchiveBrowser(addMessage('bot', ''))`.
- `commands.js` — a `helpEntries` row (`{sig, desc, notes}`) beside the other `/archive-*`
  entries at line 2312, noting that it needs the host `archive-agent`.
- `autocomplete.js` — one `SLASH_COMMANDS` entry (`args: ''`) after the `/archive-*` block
  at lines 9–11.
- `chat.css` — a new commented section with `.arch-*` classes, near the `/jobs` block.

Not added to `SETTINGS_MENU` — that menu is for configuration commands, which this is not.

## Files touched

| File | Change |
|---|---|
| `archive_browse.py` | **new** — lease watchdog, modelled on `idle_lock.py` |
| `config.py` | `ARCHIVE_BROWSE_TIMEOUT_SECONDS` |
| `app.py` | 6 routes, `_archive_browse_open/_close`, `_archive_path`, `configure()` wiring; `note_closed()` at the four existing unmount sites |
| `static/js/archive.js` | **new** — `renderArchiveBrowser` |
| `static/js/slideshow.js` | injectable `deleteMedia`, defaulted to today's behaviour |
| `static/js/dom.js` | `deleteArchiveFile` |
| `static/js/lightbox.js` | guard non-gallery URLs out of delete |
| `static/js/utils.js` | `archiveBreadcrumb`, `archiveParentPath`, `joinArchivePath` |
| `static/js/commands.js`, `autocomplete.js`, `static/css/chat.css` | command branch + help + autocomplete + styles |
| `tests/test_archive.py` | new `TestArchiveExplore` class |
| `tests/test_archive_browse.py` | **new** — the watchdog, standalone |
| `tests/js/utils.test.js` | the three path helpers |

## Verification

**Python** — a new `TestArchiveExplore(unittest.TestCase)` in `tests/test_archive.py`,
reusing the `FakeAgent` Unix-socket harness at line 18 (it already emulates
mount/unmount/status, writes the `.comfy-archive` marker into a temp mount dir, records
every request, and carries `skip_marker` / `host_mounted` flags for the failure paths).
Following house convention the first two cases are `test_requires_auth` (302) and
`test_not_configured` (503), then: listing one level and nested; `../` and absolute paths
rejected with 400; `skip_marker` refusing rather than reading plain disk; 409 while
`host_mounted`; recursive `/media` ordering; delete removing a file and refusing a
directory; `close` issuing `unmount`; and `[r["action"] for r in agent.requests]`
asserting the mount/unmount sequence, as the existing tests do.
`tests/test_archive_browse.py` drives the watchdog directly with a short timeout, as
`tests/test_idle_lock.py` does.

```bash
python -m pytest tests/test_archive.py tests/test_archive_browse.py -v
python -m pytest tests/test_idle_lock.py tests/test_logoff.py tests/test_app_routes.py -v  # no regressions
./scripts/test-all
npm run test:js
node --check static/js/archive.js static/js/slideshow.js static/js/commands.js static/js/utils.js
```

That last line is not optional — `CLAUDE.md` records the Edit tool silently turning ASCII
quotes into curly ones in JS, which fails the whole script parse silently.

**End to end**, against the live rig — the encrypted volume and the host `archive-agent`
cannot be exercised locally:

1. `/archive-session test-archive` to put a known folder on the volume.
2. `/archive-explore` → the browser opens at the root and shows `staging/`.
3. Descend into `staging/test-archive/`; check thumbnails and the breadcrumb, and that
   `‹` / a breadcrumb segment climbs back.
4. `▶ Slideshow` → confirm stills hold 3s, clips play to their own length, and ← →,
   swipe and fullscreen behave as `/slideshow-session`.
5. Delete one file from the slideshow and one from a thumbnail; confirm both vanish and a
   re-open of the folder agrees.
6. Press **Close archive**, then run `/fscheck` — it needs the volume unmounted, so it
   passing is the real proof the lease released.
7. Leave the panel open and idle past `ARCHIVE_BROWSE_TIMEOUT_SECONDS`; confirm the
   container log shows the auto-close, and that clicking a folder afterwards transparently
   re-mounts.
8. With `m` running on the host, `/archive-explore` must refuse with the ``run `m -u` ``
   message.

## Follow-up

Per `CLAUDE.md`, commit this plan to `plans/` before implementing, then rewrite it as
`ADR/archive-explore.md` (Context → Implementation → Consequences → Tests, the shape the
existing 13 ADRs use), recording the lease design, the refuse-while-host-mounted rule and
the mid-stream-unmount limitation. `CLAUDE.md` gains a short section beside the existing
archive and `m` ones. Release via the `push-to-portainer` skill, pausing for explicit
approval before the Portainer redeploy.
