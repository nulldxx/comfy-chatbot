# Delete a chat's media when the chat is deleted

## Context

Deleting a chat from the sidebar (`🗑` on a `.sel-row`) calls
`DELETE /api/chats/<name>`, which calls `persistence.delete_session()` and unlinks
**only the session JSON**. Every image and video the chat generated stays in
`IMAGES_DIR` forever, orphaned — nothing else references it, and the only way to
clear it is `/purge`-style bulk deletion or hunting it down in `/review-all`.

Wanted: deleting a chat also deletes the media it generated, except media that was
archived (`/archive-session` **moves** files — `api_archive` copies to the archive
volume then `src.unlink()`s the original, so archived media is simply *absent* from
`IMAGES_DIR` and a delete pass over it is a no-op; no extra archive check is needed).

Two decisions taken with the user:

1. **Confirmation is required.** Sidebar delete is currently one click with no
   confirm at all, which is tolerable when only a JSON file dies and not when
   images do. The row turns into an inline confirm strip — no native `confirm()`,
   because none is used anywhere in this codebase.
2. **Shared media is kept.** The same `/images/<file>` URL really can be listed by
   more than one session JSON — the `/review` grid's "🫳 Import into this session"
   (`grids.js:168`), `/jobs`' `pullAssetsIntoChat` (`commands.js:888`), a tab
   reattached to a server-side run writing into a *different* `recording_name`, and
   `_restore_session` re-importing a backup under a new name (`app.py:2440`) all
   produce it, and nothing reference-counts. Before unlinking, the other
   `sessions/*.json` are scanned and any file still referenced is skipped, so
   deleting one chat can never punch a hole in another.

## Server

### `persistence.py`

Add two helpers next to the existing session functions (they belong here — this
module already owns `sessions_dir()`, `load_session()`'s
"filter to files that still exist" logic, and imports `IMAGES_DIR` / `MEDIA_EXTS` /
`secure_filename`):

- `session_media_filenames(doc)` — bare filenames for every media file a session doc
  references: `doc["sessionImages"]` plus every `msg["images"]` in `doc["messages"]`
  (a bot message's images, in case the two ever disagree). Validate exactly as
  `load_session()` already does: `secure_filename(url.rsplit("/", 1)[-1])`, must equal
  the raw name, suffix must be in `MEDIA_EXTS`. Returns a `set`.
- `_session_media_in_use(exclude)` — union of `session_media_filenames()` over every
  `sessions/*.json` except `<exclude>.json`, skipping unreadable files, **plus each
  doc's `settings.references.images`** (`state.js:224`): a `/images/…` URL pinned as
  a reference in chat B is a live use of that file even though it is not in B's
  `sessionImages`, and `load_session` does not filter references, so a dangling one
  is not self-healed. Cheap: the session files are small JSON and there are tens of
  them.

Change the signature to `delete_session(safe_name, delete_media=False)`, returning a
dict `{"deleted_media": [names...], "kept_shared": n}` (empty/zero when
`delete_media` is false, so existing callers are unaffected apart from ignoring a
return value). Order of operations matters:

1. Raise `FileNotFoundError` if the session file is missing (unchanged).
2. If `delete_media`, read the doc and compute
   `targets = session_media_filenames(doc) - _session_media_in_use(safe_name)`
   **before** deleting anything.
3. `path.unlink()` the session JSON **first** — deleting the chat must succeed even
   if a media unlink then fails.
4. `(IMAGES_DIR / name).unlink(missing_ok=True)` for each target, inside
   `try/except OSError` so one stubborn file doesn't abort the rest. `missing_ok`
   is what makes archived media a silent no-op.

### `app.py`

- `api_chat_delete` (`app.py:3548`) — read `request.args.get("media") == "1"`, pass it
  as `delete_media`, then `seed_store.forget(name)` for each returned filename (the
  same cleanup `api_delete_image` at `app.py:2586` does). **`seed_store.forget` must
  be called from `app.py`, not `persistence.py`** — `seed_store` imports
  `persistence.atomic_write_json`, so the reverse import would be circular. Respond
  `{"ok": True, "deleted_media": N, "kept_shared": M}`.
- New `GET /api/chats/<name>/delete-preview` (`@login_required`,
  `@requires_output_storage`, same `secure_filename` guard) returning
  `{"media": N, "shared": M}` — the honest count of files that *would* be deleted
  (exists on disk, not referenced elsewhere). Needed because the sidebar's existing
  `image_count` from `list_sessions()` is `len(sessionImages)` with no disk check, so
  it overstates once anything has been archived or individually deleted; that number
  drives an irreversible click and must be true. Fetched only on the `🗑` click, not
  on every list refresh.

## Client

### `static/js/sidebar.js`

Rework `deleteChat(row, name, delBtn)` (`sidebar.js:128`) into a two-step confirm,
modelled on the existing `startInlineRename` (same row-mutation pattern, same
`refreshChatList()`-as-cancel):

1. `🗑` click → replace the row's `.sel-btn` / `✏` / `🗑` with a confirm strip:
   a non-clickable label (`.sidebar-confirm-label`, red border) reading
   `Delete chat?` while the preview is in flight, then
   `Delete + N media?` (or `Delete chat?` when `N === 0`), plus `✓` and `✕` buttons.
2. `✓` → `DELETE /api/chats/<name>?media=1`; on success `row.remove()`,
   `renderEmpty()` if the list is now empty, and dispatch `chats-changed`.
   On failure, restore via `refreshChatList()`.
3. `✕` / `Escape` → `refreshChatList()`, exactly as rename's `cancel()`.
4. If the deleted chat is the open one (`name === getRecordingName()`), call
   `newChat()` — otherwise the chat stays on screen with every `<img>` now 404ing.
   `newChat` is already in the `initSidebar(deps)` object.

### `static/css/chat.css`

One rule for `.sidebar-confirm-label` (near the existing `.sidebar-rename-input` at
`chat.css:104`): same box as `.sel-btn` but `flex: 1`, red border `#ef4444`, muted
red text, ellipsised. The `✓`/`✕` buttons reuse `.sel-del-btn` / `.sel-rename-btn`
so no new button styling is needed. Note `#sidebar-list` hides row buttons until
hover — the confirm strip's buttons need `opacity: 1` regardless.

**After any JS edit run `node --check static/js/sidebar.js`** (the Edit tool can
silently insert curly quotes — see Known Pitfalls in CLAUDE.md).

## Tests

- `tests/test_persistence.py` — beside the existing `test_delete_session` at
  `test_persistence.py:269`: `session_media_filenames` over a doc with both
  `sessionImages` and message images, rejecting traversal/non-media names;
  `delete_session(delete_media=True)` deletes the listed files, leaves a file another
  session lists, tolerates a file that is already gone (the archived case), and still
  deletes the JSON when a media unlink fails.
- `tests/test_app_routes.py` — near the existing chat-delete test at
  `test_app_routes.py:1514`: `DELETE /api/chats/x` alone leaves media on disk;
  `?media=1` removes it and its `seed_store` entry; a file another chat lists — via
  `sessionImages` in one case and via `settings.references.images` in another —
  survives and is counted in `kept_shared`; `/delete-preview` reports the
  post-archive count, not `len(sessionImages)`.
- `tests/test_storage_ready.py` — add `/api/chats/<name>/delete-preview` to the
  `@requires_output_storage` 503 sweep (`test_storage_ready.py:64`).

## Verification

```bash
cd /home/ben/Code/comfy-chatbot
node --check static/js/sidebar.js
python -m pytest tests/test_persistence.py tests/test_app_routes.py tests/test_storage_ready.py -v
./scripts/test-all
```

End-to-end in the running app: generate two images in a new chat, save it, open the
sidebar, archive one of the two via `/archive-session`-style selection, then delete
the chat — the confirm strip should offer `Delete + 1 media?` (not 2), the surviving
gallery file should vanish from `/review-all`, and the archived copy should still be
listed by `/archive-explore`. Repeat with a chat whose image another chat also lists
and confirm the file survives and `kept_shared` is non-zero.
