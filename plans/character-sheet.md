# `/character-sheet` — 7-panel 3840×2160 character sheet from one portrait

## Context
Reference-to-video models such as MiniMax H3 R2V work better from a character sheet than
from a single portrait. `/character-sheet` takes a detailed head-and-shoulders image and
runs the **current image2image workflow** (expected: Klein 9B) once per view. It then
composites the seven results onto a white 16:9 3840×2160 PNG. Each panel is cached in
the table, so you can regenerate one panel and re-composite without re-running the rest.

Decisions taken: panel size reaches the workflow through new optional `<WIDTH>`/`<HEIGHT>`
tokens; denoise is fixed at **1.0**; the sheet has **no captions**.

Per repo rules: first commit this plan as `plans/character-sheet.md`; when the work is done,
rewrite it as `ADR/character-sheet.md` and add a CLAUDE.md section.

## Sheet layout (`character_sheet.py`, the single source of truth)
Canvas 3840×2160, white, 40px margin and gutters.

| key | default prompt | panel box (x,y,w,h) | generated at |
|---|---|---|---|
| `portrait` | The person, head and shoulders view, white background | 40,40,1560,1560 | 1536×1536 |
| `front` | The person, full body view facing forwards, white background | 1640,40,693,2080 | 768×1920 |
| `side` | The person, full body profile view, white background | 2373,40,693,2080 | 768×1920 |
| `rear` | The person, full body rear view, white background | 3106,40,693,2080 | 768×1920 |
| `neutral` | The person, with a neutral facial expression | 40,1640,480,480 | 1024×1024 |
| `smile` | The person, with a smiling facial expression | 580,1640,480,480 | 1024×1024 |
| `shocked` | The person, with a shocked facial expression | 1120,1640,480,480 | 1024×1024 |

The left half holds the large portrait with the three expressions beneath it. The right
half holds three tall full-body views. Your default prompts are kept, with the typos
"fully"/"fowards" fixed.
- **Fit**: each result is scaled to fit its box, centred, on white. A mismatched aspect
  ratio is harmless because the letterbox is also white.
- **Full-body trim**: before fitting, near-white borders are cropped (pixels > 245, plus 3%
  padding) so all three figures fill the panel height and come out the same scale. The
  portrait and expressions are not trimmed.
- Pure Pillow, with no app imports, so it is testable on its own (like `face_multi.py`).
  `PANELS` holds key, generation size and box. `compose_sheet({key: Path}) -> Image`,
  `trim_white(img)`, `fit_into(canvas, img, box)`.

## Backend
1. **`<WIDTH>`/`<HEIGHT>` tokens** (`workflow.py`, `generation_service.py`):
   - New bare-numeric slots. Add them to `fill_placeholders_for_validation`.
   - `_run_generation_core` gains `out_width`/`out_height` kwargs and fills the tokens only
     when the template contains them.
   - With no size given (the ordinary ✨ i2i button), they fall back to the **input image's
     size rounded to a multiple of 16**. This keeps a template with the tokens working for
     plain i2i, instead of failing the existing "Unfilled workflow placeholders" check.
   - A template without the tokens simply ignores the size, and compositing fits whatever
     comes back.
   - **Template edit** (`/data/Downloads/klein9b-image-edit.json`, the file is directly
     in Downloads):
     - Set `width`/`height` on `EmptyFlux2LatentImage` (`75:66`) and on `Flux2Scheduler`
       (`75:62`) to bare `<WIDTH>`/`<HEIGHT>`. Today both take their size from
       `GetImageSize` (`75:100`) of the 1 MP-scaled input.
     - Leave `GetImageSize` in place: nothing else uses it, and ComfyUI prunes unused
       nodes.
     - It is a true edit graph (empty latent + `ReferenceLatent`) with no `<DENOISE>`, so
       the denoise of 1.0 is harmless.
     - **Fallback size** without an explicit one: the input scaled to **1 MP**, rounded to
       a multiple of 16. That reproduces today's behaviour (`ImageScaleToTotalPixels`
       megapixels=1) for the ordinary ✨ button.
     - You then copy the edited file to `~/comfy-workflows/image2image/` on
       `$PROD_SERVER`.
2. **Job** `run_character_sheet` / `start_character_sheet_job` (`generation_service.py`):
   - Modelled on `run_face_detail_multi` (lines ~878–1010): same job record, channel,
     cancel and terminal handling.
   - Input: `source: Path`, `panels: [{key, prompt, loras, cached: Path|None}]`, `compose: bool`.
   - For each panel with no cached path, call `_run_generation_core(...,
     workflow_dir=COMFY_IMAGE2IMAGE_DIR, input_image=source, denoise=1.0,
     out_width/out_height=PANELS[key] size)`, with a random seed as usual.
   - After each panel, emit a new SSE event `{"type":"panel", key, url}` and also append the
     url to `jobs[id]["assets"]`, so `/jobs` can recover panels if the tab closed.
   - Progress messages read `Panel 3/7 — Side view…`.
   - When `compose` is set, call `character_sheet.compose_sheet`, save it as
     `<ts>_charsheet_<hex>.png` in `IMAGES_DIR`, and send `done` with `images:[sheet]` and
     `panels:{key:url}`.
   - Panel images are ordinary gallery files and are **kept**, since they are the cache.
3. **Route** `POST /api/character-sheet` (`app.py`, beside `api_face_detail_multi`):
   - Decorators: `@login_required`, `@requires_output_storage`.
   - Body: `{image, workflow?, panels:[{key, prompt, cached?}], compose, server?, server_os?}`.
   - Validation, each failure a 400:
     - keys must be in `PANELS` and unique;
     - each prompt must be non-empty after `parse_loras_from_prompt`;
     - `image` and each `cached` URL go through `resolve_input_image`; a cached file that no
       longer exists is treated as a miss and regenerated, not an error.
   - The workflow comes from `resolve_workflow(..., list_image2image_workflows(), "image2image")`.
     This is the same allowlist as `/api/image2image`.
   - Regenerating one panel is the same route with one panel, no `cached`, and
     `compose:false`.
4. `Dockerfile`: `COPY character_sheet.py`. Pillow is already a dependency.

## Frontend — new `static/js/charsheet.js`
- **`/character-sheet` command** (`commands.js`, plus a help entry and an autocomplete entry):
  - It posts: "Uses your image2image workflow **<name>** (change with `/i2i-workflow`)…"
  - It then renders the table bubble.
- **Table**:
  - **Top row**: the input-image drop zone. It accepts in-app media drags
    (`COMFY_URL_DND_TYPE`, images only) and desktop files, which are uploaded via
    `/api/import-image`. Click to browse. ✕ clears it.
    - This reuses the drop-zone pattern from the `/references` row (`commands.js` ~400–450).
      Export a small `uploadImageFile` helper from there, or copy the ~10 lines.
  - **Seven rows** of label + editable prompt textarea on the left, and the result
    thumbnail on the right. Each thumbnail has a ↺ overlay in its bottom-right corner, which
    regenerates that panel with the textarea's current prompt.
    - Thumbnails open in the lightbox. They are `/images/` URLs, so the right-click menu
      works for free.
    - While a panel is running, its cell shows a marquee.
  - **Bottom**: a ⟲ *Reset prompts* link and a **Generate** button.
    - Generate posts every panel, attaching `cached` for panels that already have a result,
      with `compose:true`.
    - It streams through `/api/progress/<id>` and fills cells on `panel` events.
    - On `done` it appends the sheet to the chat as a normal image (`state.sessionImages`
      push, `appendChatImage`, prompt "Character sheet"). Generating again re-composites from
      the cache.
    - Generate and ↺ are disabled while a job is running. A ✕ cancel button sits beside the
      status line.
  - **↺ on a panel**: runs a single-panel, `compose:false` job and deletes the superseded
    panel file (`deleteImageFile`), as do-over does.
  - Changing the input image clears every cached panel, because the old panels show a
    different person.
- **State** (`state.js`): `state.characterSheet = { source, prompts:{key}, results:{key:url} }`.
  The defaults live in `CHARACTER_SHEET_PANELS` in `utils.js`, with keys, labels and default
  prompts only; sizes stay server-side.
  - It persists as a top-level session field, as `videoStory` does (`saveSession`/
    `restoreSession` in `chat.js`). `newChat` resets it.
  - It is not in `/settings-save`, because it is content rather than a setting.
  - Prompt edits save on `input`.
  - Running `/character-sheet` again reopens the table with the cached state.
- **Chat delete**: add `characterSheet.results` URLs and `source` to `session_media_filenames`
  (`persistence.py`), so deleting a chat removes its panels. A source image imported from
  the desktop is in `sessionImages` anyway. Shared-file protection already handles a source
  image that another chat also uses.
- Styling: use theme tokens only (`chat.css`). Add `.charsheet-thumb` to the privacy-mode
  mask selector list.

## Tests
- `tests/test_character_sheet.py`:
  - every panel box lies inside the canvas, and no two boxes overlap;
  - `trim_white` on a synthetic figure;
  - `fit_into` keeps the aspect ratio and centres;
  - `compose_sheet` yields 3840×2160 with white margins.
- Route tests (pattern from `tests/test_face_detail_multi*.py`, with the job start mocked):
  - 400s for an unknown or duplicate key, an empty prompt, a bad image, and a bad workflow;
  - a missing cached file becomes a regenerate;
  - `compose` is forwarded.
- `<WIDTH>`/`<HEIGHT>` tests:
  - the explicit size is filled;
  - the fallback is the input size rounded to 16;
  - a template without the tokens is unaffected;
  - the validation filler parses.
- JS:
  - `node --check` on every edited JS file (curly-quote pitfall);
  - Jest for any pure helper added to `utils.js`, for example the payload builder that
    attaches `cached`.
- Run `./scripts/test-all` and `npm run test:js`.

## Verification (end to end)
1. Add `<WIDTH>`/`<HEIGHT>` to the Klein i2i template on `$PROD_SERVER` (or a local copy).
2. `docker-compose up --build -d`, then log in and run `/character-sheet`. Check that the
   i2i workflow reminder appears.
3. Drag a chat portrait in and press Generate. Seven panels should fill one by one, and the
   sheet image should appear in chat at 3840×2160 (check with Inspect…). The full-body views
   should be tall and similar in scale.
4. ↺ the smiling panel, edit its prompt, and press Generate again. Only the composite should
   be rebuilt (no new ComfyUI runs except the edited panel), and the sheet should show the
   new smile.
5. Reload the page and run `/character-sheet` again. The cached results and prompts should
   restore.
6. Then release via the `push-to-portainer` skill, asking before the Portainer stage.
