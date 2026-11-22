# ADR: `/character-sheet` — a 3840×2160 character sheet from one portrait

## Status
Implemented. Plan: `plans/character-sheet.md`.

## Context
Reference-to-video models such as MiniMax H3 R2V work better from a character sheet
(several views of the same person on a white background) than from a single portrait.
Making one by hand meant running the image2image workflow seven times, picking the
results, and laying them out in an editor.

## Decision
`/character-sheet` shows a reminder naming the active image2image workflow, then a table:

- an **input image** box at the top (drag from the chat, or drop or browse a desktop
  file, which is imported via `/api/import-image`);
- seven rows, each with an editable prompt on the left and its result on the right:
  portrait, front, side and rear full body, and neutral, smiling and shocked expressions;
- a ↺ in the bottom-right corner of each result, which re-runs just that panel with a
  new seed and the current prompt;
- a **Generate** button.

Generate sends every panel to `POST /api/character-sheet`. Panels that already have a
result go as `cached`, so the server runs only the missing ones, then composites the
sheet. The sheet is added to the chat as an ordinary image.

### Layout (`character_sheet.py`)
`PANELS` is the single source of the layout: the generation size and the sheet box for
each panel.

- The canvas is 3840×2160, white, with 40px margins and gutters.
- The left half is a 1560² portrait above three 480² expressions.
- The right half is three 693×2080 full-body columns.
- Each result is fitted onto white, keeping its aspect ratio. Full-body results are first
  trimmed of near-white borders (> 245, with 3% padding), so all three figures fill the
  height at the same scale.
- There are no captions: images only, which is the cleanest input for a reference model.

The module is pure Pillow and testable on its own, like `face_multi.py`.

### Panel shapes: `<WIDTH>`/`<HEIGHT>`
Full-body views need tall frames (768×1920). Edit graphs normally take their size from
the input image, so two new optional **bare numeric** tokens were added.

- `_run_generation_core(out_width=, out_height=)` fills the tokens only when the template
  contains them.
- With no explicit size (the plain ✨ i2i button), they fall back to the source image
  scaled to **1 MP** and snapped to a multiple of 16. That is what the Klein template's
  `ImageScaleToTotalPixels(megapixels=1)` → `GetImageSize` chain produced before, so
  adding the tokens doesn't change ordinary i2i.
- A template without the tokens still works: the size is ignored and the fit absorbs the
  aspect mismatch.
- `apply_resolution` was rejected for this. It overwrites `width`/`height` on *every*
  node, which in an edit graph includes nodes that resize the input image.
- `klein9b-image-edit.json`: the `EmptyFlux2LatentImage` (`75:66`) and `Flux2Scheduler`
  (`75:62`) width/height now read `<WIDTH>`/`<HEIGHT>`.

### Server job
`run_character_sheet` (`generation_service.py`) follows the `run_face_detail_multi` job
shape.

- Each uncached panel is one `_run_generation_core` call:
  - against `COMFY_IMAGE2IMAGE_DIR`;
  - with the source image as `<INPUT_IMAGE>`;
  - with **denoise fixed at 1.0**, because a new pose has to be redrawn entirely and the
    source is the identity reference, not a starting latent.
- It emits two new SSE events, `panel_start` `{key}` and `panel` `{key, url}`, and appends
  each panel to `assets`, so `/jobs` can recover panels if the tab closed.
- `done` carries `images` (the sheet, or `[]` with `compose: false`) and `panels`. `error`
  and `cancelled` carry the panels finished so far.
- Panel files are **kept**, because they are the cache. Only a multi-output graph's extra
  files are discarded.

The route validates panel keys (known, unique), prompts (non-empty unless the panel is
cached), the image and the workflow (the i2i allowlist) before starting anything. A
`cached` URL whose file has gone is regenerated rather than rejected.

### Client (`charsheet.js`)
- `state.characterSheet = { source, prompts, results }`, normalised by
  `normalizeCharacterSheet` (`utils.js`).
  - It is saved with the chat session and redrawn at the end of a restored chat, as
    `/video-story` is. `newChat` resets it.
  - It is not in `/settings-save`: it is content, not a setting.
- Tables redraw on `character-sheet-changed` (structure) and `character-sheet-job`
  (progress). A prompt edit sends only `character-sheet-edited`, which saves without a
  redraw.
- One job per tab (`activeJob`, module-level). A second open table shows the same
  progress and cannot start a parallel job.
- A regenerated panel deletes the file it supersedes, as do-over does.
- A thumbnail that fails to load (deleted or archived) is forgotten, so the next Generate
  re-runs that panel.
- Changing the input image clears the results but leaves their files in the gallery.
- Desktop drops onto the input box set `e.comfyDropHandled`. The page-wide drop handler
  still closes its overlay but no longer also imports the file into the chat.

### Chat delete
`session_media_filenames` counts `characterSheet.results`, so deleting the chat deletes
its panels. The **source** image is treated like a reference instead: another chat's
sheet source counts as "in use", but a chat's own source is not deleted with it.

## Consequences
- The template needs the tokens for good full-body framing. Without them the panels are
  still generated, but squarish and letterboxed.
- Panels are gallery files and show up in `/review-all` and similar views.
- The job runs server-side, but the client records panels into the session from SSE. A
  tab closed mid-run loses the table's record of those panels, though the files remain
  and are listed under `/jobs`.
- Generation is sequential: seven Klein runs take minutes, not seconds.
