# ADR: Deleting a chat deletes the media it generated

**Status:** Implemented.
**Scope decided before build:** deletion is opt-in per click behind an **inline confirm
strip** (not a native `confirm()`, not a one-click delete); media referenced by **another
chat is kept**; archived media needs **no special case**.

## Problem

Deleting a chat from the sidebar called `DELETE /api/chats/<name>` →
`persistence.delete_session()`, which unlinked the session JSON and nothing else. Every
image and video that chat generated stayed in `IMAGES_DIR` with nothing left pointing at
it. The gallery is the only place it then existed, so clearing it meant finding each file
by eye in `/review-all` or reaching for `/delete-all`.

The chat is the natural unit of "this work": if it is gone, its renders usually should be
too.

## Decision

`DELETE /api/chats/<name>?media=1` deletes the chat **and** the media it generated. The
flag is opt-in on the wire so the bare route keeps its old meaning; the sidebar always
passes it.

### Archived media needs no check

`/archive-session` & friends **move**: `api_archive` copies each file to the archive
volume, verifies the size, then `src.unlink()`s the gallery original. An archived image is
therefore already absent from `IMAGES_DIR`, and `unlink(missing_ok=True)` over it is a
no-op. "Delete unless archived" falls out of the existing design rather than needing an
archived-file marker or an index — which is just as well, since a session-scope archive
renames files on the way in (`<folder>001.png`), so the archived copy does not even keep
the name the session knows it by.

### Shared media is kept

One gallery file really can belong to several chats, and nothing reference-counts:
`/review`'s *🫳 Import into this session* (`grids.js`), `/jobs`' `pullAssetsIntoChat`
(`commands.js`), a tab reattached to a server-side run whose `recording_name` is a
different chat, and `_restore_session` re-importing a backup under a new name all
duplicate a URL. So `_session_media_in_use(exclude)` reads every *other*
`sessions/*.json` first and any file still referenced is skipped and counted in
`kept_shared`. The scan is a few small JSON reads and only happens on a delete.

It counts `settings.references.images` (and the pre-`/references` `settings.refImageUrl`)
as a live use, not just `sessionImages`. A reference is a file a chat *points at* rather
than one it made, and — unlike `sessionImages` — `load_session` never filters references
to files that still exist, so a dangling one would not self-heal into invisibility.

That asymmetry is deliberate and is why there are two functions:
`session_media_filenames(doc)` (what a chat *owns* — `sessionImages` plus each bot
message's `images`, since a doc can carry an image in one and not the other) excludes
references; the in-use scan includes them.

### The chat JSON is unlinked first

Deleting the chat is what the user asked for and must not be lost to a media unlink that
fails on a busy file. So: compute the target list → unlink the JSON → unlink the media,
each file in its own `try/except OSError`. `seed_store.forget()` runs in `app.py` over the
returned names, because `seed_store` imports `persistence` and the reverse would be
circular.

### A true number, not the one already on screen

Sidebar delete used to be a single click with no confirmation, which is fine when a JSON
file dies and not when images do. The row now turns into a confirm strip
(`Delete + N media?` / `✓` / `✕`), modelled on the inline rename next to it — there is no
`window.confirm` anywhere in this codebase, and the existing y/n idiom
(`state.pendingConfirm`) is tied to the chat input box and unreachable from a sidebar row.

`N` comes from a dedicated `GET /api/chats/<name>/delete-preview`, fetched on the 🗑 click
only. The sidebar's list already carries `image_count`, but that is `len(sessionImages)`
with no disk check, so it overstates the moment anything has been archived or deleted
individually. A number that drives an irreversible click has to be true, and parsing it
per row on every list refresh would not be.

Confirming the currently-open chat also calls `newChat()`: its images have just been
deleted, and leaving it on screen would show a wall of broken media.

## Consequences

- **Deleting a chat is now destructive.** The confirm strip is the only thing between a
  stray click and the renders; `/session-save`-then-delete is no longer free.
- **No undo, no recycle bin.** Archive first if the work matters — which is now the
  meaningful distinction between the two commands.
- **A chat sharing an image with another chat leaves it behind**, so deleting every chat
  one at a time can still leave files: the last chat holding a shared file is the one that
  deletes it.
- **Not covered:** the bare `DELETE /api/chats/<name>` (no `?media=1`) still deletes only
  the JSON, so any other caller is unchanged; nothing retroactively cleans up media
  orphaned by chats deleted before this change.

## Files

- `persistence.py` — `_media_filename`, `session_media_filenames`,
  `_session_media_in_use`, `delete_session(safe_name, delete_media=False)`,
  `session_delete_preview`.
- `app.py` — `api_chat_delete` (`?media=1` + `seed_store.forget`),
  `api_chat_delete_preview`.
- `static/js/sidebar.js` — `deleteChat` two-step confirm strip.
- `static/css/chat.css` — `.sidebar-confirm-label`, `.sidebar-confirm-btn`.
- `tests/test_persistence.py`, `tests/test_app_routes.py` (`TestChatDeleteMedia`),
  `tests/test_storage_ready.py`.
