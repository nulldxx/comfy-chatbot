# ADR: `/face-detail-super <N>` — N-way face detail with a tile picker

**Status:** Implemented.

## Problem

The per-image face-detailer icon (👤) ran the detailer **once** and presented the result
against the original as a before/after wipe slider with a 1/2 picker
(`buildComparisonSlider`, `editors.js`). There was no way to explore several detailer
variations of the same face and pick the best — you accepted/rejected blindly and re-ran
if unhappy.

## Decision

Add a `/face-detail-super <N>` command that toggles the face icon into an **N-variation
mode**: clicking it runs the detailer N times on the same source image (each with a fresh
random seed, which the pipeline already does) and shows a **tiled grid cropped to the
face** with **numbered buttons 1…N** to pick the preferred variation. `/face-detail-super
1` restores the normal single+slider behaviour.

Confirmed choices: (1) a session **mode toggle** rather than a one-shot; (2) tiles
**cropped to the face**, located by diffing a result against the original **client-side**
(`<canvas>`, same-origin ⇒ untainted `getImageData`) so **no Python image dependency** is
added; (3) picking a tile **keeps the chosen result and discards the rest** — it replaces
the original in the session (inheriting its prompt + gallery slot) and deletes the
original and the other N−1 results; an "orig" button rejects all N.

## How it was implemented

### Backend
- **`generation_service.py`** — new `run_face_detail_super(job_id, …, count)` +
  `start_face_detail_super_job(…)`. The job loops `count` times calling the existing
  `_run_generation_core` with identical face-detail inputs (only the seed varies, via
  `randomize_seeds`), emits `progress` `"Detail i/N…"` per pass, collects all URLs and
  returns them in one terminal `done` event. Mirrors `run_generation`'s lifecycle
  ownership (`_mark_terminal_locked` + `channel.close()`); honours `cancel_event` between
  passes. Streams over the existing `/api/progress/<job_id>` SSE unchanged.
- **`app.py`** — `api_face_detail()` gains an optional `count` (validated up front to
  2–16; absent/1 ⇒ unchanged single-generation path). `count > 1` dispatches to
  `start_face_detail_super_job` instead of `start_generation_job`.

### Frontend
- **`state.js`** — `faceSuperN` (default 1; a session mode, not reset by `newChat`).
- **`commands.js`** — `/face-detail-super <N>` sets `state.faceSuperN` (1 = off), with
  `/help` + `autocomplete.js` entries.
- **`chat.js`** — the face-icon click branches on `state.faceSuperN > 1` to a new
  `runFaceDetailSuper`, which posts `count` and passes `opts.superTileReplace` (instead of
  `sliderReplace`). `runGeneration`'s `done` handler gained a `superTileReplace` branch
  (before the slider branch) that renders the tile picker; its pick/reject handlers reuse
  the slider's exact metadata-move + `deleteImageFile` cleanup logic.
- **`utils.js`** — pure `computeDiffBox(a, b, w, h, opts)` returns the padded, clamped
  bounding box of changed pixels (or `null` for no/small/mismatched change → full-image
  fallback). Unit-tested in `tests/js/utils.test.js`.
- **`editors.js`** — `buildFaceSuperTiles(originalUrl, resultUrls, {onPick,
  onKeepOriginal})` renders the numbered buttons + a `<canvas>` tile grid. `renderFaceTiles`
  diffs the original against the first variation (via `computeDiffBox`) to find the crop
  rect once, then draws each variation's crop into its tile; falls back to the whole image
  on any error or size mismatch. Settle-once semantics mirror the slider.
- **`chat.css`** — `.fs-container/.fs-picks/.fs-pick(-orig)/.fs-tiles/.fs-tile(-badge)`,
  modelled on the existing `.ba-*`/review-grid styles.

## Tests
- **JS** (`tests/js/utils.test.js`): `computeDiffBox` — identical → null; a changed
  rectangle → tight padded box; edge clamping; sub-`minFrac`/sub-threshold noise → null;
  size mismatch → null.
- **Python** (`tests/test_simple.py`): `/api/face-detail` count validation (non-integer,
  out-of-range) and that a valid `count` clears the count check (auth + fail-fast paths).

## Notes / caveats
- The face crop needs the result and original to share dimensions (detailer output does).
  If they differ, or the diff finds nothing meaningful, tiles show the full image — the
  feature degrades gracefully rather than failing.
- On cancel/error the source image wrap is left untouched (original preserved), matching
  the slider path.
