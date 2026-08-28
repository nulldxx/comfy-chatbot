# Plan: enable/disable toggles for `/references` rows

## Problem

The `/references` table is all-or-nothing per row: the only way to stop feeding a
reference to a workflow is `✕`, which throws the URL away. Re-attaching means finding
the chat media or re-uploading the file. Video rows already have a partial answer (untick
both track boxes → "inactive"), but images and standalone audio have none, and even for a
video "untick both, then remember what was ticked" is not what a user means by *off*.

There is also a budget reason: the 12-file cap makes swapping between reference sets
routine, and today that means deleting entries you want back.

## Approach

A per-row **enabled** flag, uniform across all 15 rows, sitting alongside the existing
per-video track flags rather than replacing them.

- `state.references.enabled = { images: [9], videos: [3], audios: [3] }`, booleans,
  default `true`. Same group keys as `REFERENCE_SLOT_COUNTS`, so it pads/migrates the
  same way (an older session with no `enabled` key restores as all-on).
- **Disabled ⇒ costs nothing** against `REFERENCE_MAX_FILES` and is **not sent** — the
  point of the feature is to park a reference without paying for it. For a video row the
  flag gates the whole clip: disabled = 0 files whatever the ticks say, and the ticks are
  preserved so re-enabling restores exactly what was there.
- Enabling is a `+n` against the cap and can be refused, like ticking a track box is.
- Purely client-side: `referencesForRun` (`chat.js`) masks a disabled slot to `null`
  (and its tracks to `false`), so the wire format is unchanged and `_resolve_references`
  needs no edit. A disabled slot is indistinguishable from an empty one to the server.

## Work

1. `state.js` — `enabled` in `newReferences`/`cloneReferences` (with a `padEnabled`
   defaulting to on); `countReferenceFiles` skips disabled slots.
2. `commands.js` — a toggle switch per row; dim the row when off; disable the track
   checkboxes when off; refuse an enable that would exceed the cap. Replace the two
   ad-hoc cap checks (`refSlotWouldExceed`, `refTrackSet`) with one prospective-cost
   helper now that three different edits can change a row's cost.
3. `chat.js` — mask disabled slots in `referencesForRun`.
4. `chat.css` — a small `.ref-switch` component (no switch styling exists yet).
5. Tests — `tests/js/state.test.js` for counting, cloning and legacy migration.
6. Docs — CLAUDE.md References section, and the `/help` entry for `/references`.

## Decisions

- **Dropping into a disabled row re-enables it.** Attaching a file is an unambiguous
  "I want this"; leaving it silently off would look broken.
- **`✕` resets the flag to on**, like it already resets the track flags — an empty slot
  should never carry hidden state.
- **The switch is disabled on an empty row** (nothing to turn off), mirroring the track
  checkboxes.
- **Keep "inactive"** (a filled, enabled video with both tracks unticked) as a distinct
  state. It is reachable and means something different from *off*; the caption
  distinguishes them.
