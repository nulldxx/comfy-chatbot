# ADR: per-row enable switches in the `/references` table

## Context

The `/references` table was all-or-nothing per row: the only way to stop feeding a
reference to a workflow was `✕`, which threw the URL away. Re-attaching meant finding the
chat media again or re-uploading the file. Video rows had a partial answer — untick both
track boxes and the clip goes "inactive" — but images and standalone audio had none, and
even for a video, "untick both and remember what was ticked" is not what a user means by
*off*.

The 12-file cap makes swapping between reference sets routine, which is exactly the
situation where deleting an entry you want back is most costly.

## Decision

A per-row **enable** flag, uniform across all 15 rows, alongside (not replacing) the
per-video track flags.

`state.references.enabled = { images: [9], videos: [3], audios: [3] }`, booleans,
default `true` — the same group keys as `REFERENCE_SLOT_COUNTS`, so it pads and migrates
the same way.

**A switched-off row is charged nothing and sent as nothing.** That is the whole point:
parking a reference has to free budget, or it is just a dimmed decoration. For a video
the flag gates the entire clip — off means 0 files whatever the ticks say — and the ticks
are preserved, so switching back on restores exactly what was there.

**Client-side only.** `referencesForRun` (`chat.js`) masks an off row to `null` (and its
tracks to `false`), so the wire format is unchanged and `_resolve_references` in `app.py`
needed no edit. To the server, an off row is indistinguishable from an empty one — which
is precisely the intended behaviour, and avoids teaching the backend a UI concept.

## Implementation

- **`state.js`** — `padEnabledGroups()` builds the flag arrays; `newReferences()` and
  `cloneReferences()` carry them. `referenceSlotEnabled(refs, key, i)` reads a flag with
  **absent ⇒ on**, so a session (or payload) saved before the switches existed restores
  and counts exactly as it used to. `referenceSlotCost(key, url, enabled, tracks)` is the
  single pricing rule — 0 for empty or off, 1 for an image/audio, one per ticked track for
  a video — and `countReferenceFiles()` is now just a sum over it.
- **`commands.js`** — a `.ref-switch` per row (disabled on an empty row; the row and its
  track boxes dim/disable when off; the caption reads `off` rather than `inactive`).
  Three different edits can now change what a row charges — filling it, ticking a track,
  switching it on — so the two ad-hoc cap checks (`refSlotWouldExceed`, the inline test in
  `refTrackSet`) were replaced by one prospective-cost helper, `refWouldExceed(slot, next)`:
  *total − what this row costs now + what it would cost after*. The "one file left, land
  the video without its audio" fallback became a `fitCheck()` predicate so `acceptFile`
  can make the same decision **before** uploading, rather than uploading a file the drop
  handler would then refuse.
- **`chat.css`** — the `.ref-switch` component (no switch styling existed in the app).
- **`chat.js`** — `referencesForRun` masks off rows.
- **`tests/js/state.test.js`** — counting with flags, freeing budget by parking a clip,
  deep-copy of the flags, and the absent-flags-are-on migration.

## Consequences

- Attaching a file **re-enables** a parked row; `✕` resets the flag. An empty slot never
  carries hidden state, and a row you just dropped a file onto is never silently off.
- Off and **inactive** (on, but a video with neither track ticked) remain distinct
  reachable states. They mean different things, so the caption names each rather than
  collapsing them.
- Persistence came free: the flags live inside `state.references`, so the chat session,
  the `/settings-save` stack and `newChat` pick them up through `cloneReferences` with no
  changes at those call sites — the same property that made the video-track change cheap.
