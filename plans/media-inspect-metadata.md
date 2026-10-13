# Plan: "Inspect" on the media right-click menu

## Context

ComfyUI embeds the generation graph in its output files. Today the only way to see it
is to download the file and drop it into the ComfyUI editor. The right-click menu
(`static/js/mediamenu.js`) already offers Save / Copy / Copy seed for every `/images/`
file. This adds an **Inspect** row that posts the file's embedded metadata as a table in
a chat bubble. Each section gets a 📋 copy button, so you can lift a prompt, a seed or the
whole workflow JSON without leaving the app.

Facts that shape the design:
- Output files are written **byte-for-byte** from ComfyUI's `/view`
  (`ComfyServer.download_images` → `generation_service._run_generation_core`, renamed
  only). Embedded metadata therefore survives.
- There is **no Pillow** in `requirements.txt`. `ffprobe` **is** in the image
  (Dockerfile installs `ffmpeg`, and `app._probe_video_info` already shells out to it).
- The app is usually served over **plain HTTP on a LAN**, where `navigator.clipboard` is
  undefined. Copy needs a fallback.

## Where ComfyUI puts metadata (what we parse)

| Format | Location | Keys |
|---|---|---|
| PNG (core `SaveImage`, VHS preview PNGs) | `tEXt` / `iTXt` / `zTXt` chunks | `prompt` (API graph JSON), `workflow` (UI graph JSON), plus any others (e.g. A1111 `parameters`) |
| WEBP (`SaveAnimatedWEBP`) | `EXIF` RIFF chunk, TIFF IFD0 ASCII tags | `0x0110` = `"Prompt:{…}"`, `0x010F` = `"Workflow:{…}"` (plus any other `Key:value` tags) |
| MP4 / WEBM (core `SaveVideo`) | container format tags | `prompt`, `workflow` |
| MP4 / WEBM (VHS `VideoCombine`) | format tag `comment` (any case) | a JSON object containing `prompt` / `workflow`, sometimes as JSON strings that need a second decode |
| JPG / GIF | usually nothing | handled as "no metadata" |

## Server

### New module `media_metadata.py` (pure, no `app` import, unit-testable)

- `read_raw_metadata(path) -> dict[str, str]`, dispatching on the file extension:
  - `_png_text_chunks(path)`: walk the chunks with `struct`. Decode `tEXt` as latin-1,
    `zTXt` with `zlib`, and `iTXt` as UTF-8 (with optional compression). Stop at `IEND`.
    Cap the total bytes read (e.g. 32 MB) to guard against a pathological file.
  - `_webp_exif(path)`: walk the RIFF chunks to `EXIF`, then parse a minimal TIFF IFD0
    (both byte orders, ASCII type only). Split each value on the first `:` into
    `key → value`.
  - `_video_tags(path)`: `ffprobe -v error -show_format -of json`, take `format.tags`
    and lower-case the keys. If `comment` parses as a JSON object, merge its keys in
    (VHS). On `FileNotFoundError` / timeout / non-zero exit, return `{}` (same tolerance
    as `_probe_video_info`).
- `parse_metadata(raw) -> dict`: JSON-decode `prompt` / `workflow`, decoding twice if the
  value is a JSON string holding JSON. Returns:
  ```
  { "summary": [ {label, value, node, class_type} … ],
    "prompt":  <API graph or null>,
    "workflow": <UI graph or null>,
    "other":   { key: text } }   # unrecognised text keys, e.g. A1111 "parameters"
  ```
- `summarise_prompt_graph(prompt) -> list[row]`: walk the API graph in node-id order and
  emit the useful rows:
  - **Prompts**: string `text` / `prompt` / `positive` / `negative` inputs on any node,
    labelled `Prompt (#id ClassType title)`. The label uses `_meta.title` when present, so
    "Positive" / "Negative" titles show through. Linked inputs (`[id, idx]` lists) are
    skipped.
  - **Sampling**: `seed` / `noise_seed`, `steps`, `cfg`, `sampler_name`, `scheduler`,
    `denoise` from any node that has them. Seeds stay **strings**, as `/api/image-seed`
    does, to keep 64-bit precision.
  - **Models**: `ckpt_name`, `unet_name`, `lora_name` (+ `strength_model`), `vae_name`,
    `clip_name*`.
  - **Size**: `width` / `height` / `length` / `frame_rate` when they are literal values.
  - Keep it a flat allow-list of input names in one constant, `SUMMARY_INPUTS`, so adding
    one is a one-line change.

### Route `GET /api/image-metadata/<filename>` (`app.py`, next to `api_image_seed`)

- `@login_required`, `@requires_output_storage`. Validate the name exactly as
  `api_image_seed` does (`secure_filename` round-trip + `MEDIA_EXTS`). Return 404 if the
  file is missing.
- Returns `parse_metadata(read_raw_metadata(IMAGES_DIR / name))` plus
  `file: {name, size, mtime}` and `seed: seed_store.get_seed(name)`. The app's own
  recorded seed is a useful cross-check, and it covers JPG/GIF files with no embedded data.
- An empty result is a `200` with an empty `summary` and nulls, not an error. The client
  shows "No ComfyUI metadata embedded".

## Client

### `mediamenu.js`: new row

- Add an **Inspect** row (images and videos) after Copy seed. A click dismisses the menu
  and calls `inspectMedia(url)`.
- `inspectMedia` posts a bot bubble right away ("🔎 Reading metadata from `name`…"). It
  then fetches `/api/image-metadata/<name>` and replaces the bubble's content with the
  table. On error it shows the error text in that same bubble.

### Rendering: `renderMetadataTable(data)` in `utils.js` (pure → returns an HTML string, Jest-testable)

One `<table class="meta-table">`. Each row is a section, laid out as label | value | 📋:
1. **File**: name, size, date. Also the recorded seed, if `seed_store` has one.
2. **Summary rows**: prompts first, then sampling, then models, then size. Prompt values
   go in a `<pre class="meta-text">` so long text wraps.
3. **Other text keys** (`other`), one row each.
4. **Prompt graph (API)** and **Workflow (UI)**: each is a `<details>` (collapsed) holding
   pretty-printed JSON, with a 📋 that copies the **compact** JSON. That copy can be
   pasted straight into ComfyUI.

Every value is passed through `escapeHtml`. The copy buttons carry `data-copy-idx`. The
copy strings are kept in a JS array attached to the bubble, never placed in an HTML
attribute, so large workflow JSON stays out of the DOM attributes.

### Copy helper: `copyText(text)` in `utils.js`

`navigator.clipboard.writeText` when `window.isSecureContext`. Otherwise fall back to a
hidden `<textarea>` + `document.execCommand('copy')`, which still works over HTTP inside a
click handler. The button flashes `✓` on success or `✗` on failure, much like `run()` in
the menu does. The existing "Copy image address" row has the same plain-HTTP problem, so
switch `copyAddress` to `copyText` too (one-line fix).

### CSS (`static/css/chat.css`, next to `.media-menu`)

`.meta-table` (full bubble width, `table-layout: fixed`, label column ~9em),
`.meta-text` (`white-space: pre-wrap; word-break: break-word; max-height` with scroll),
`.meta-copy` (small borderless button), and `details > pre` for the JSON blocks. Use the
existing CSS variables so dark/light themes both work.

## Docs (per CLAUDE.md conventions)

- Commit this plan as `plans/media-inspect-metadata.md` before implementation.
- Afterwards, write `ADR/media-inspect-metadata.md`, and add an **Inspect** bullet to the
  "Media right-click menu" section of `CLAUDE.md`.

## Files

- **New:** `media_metadata.py`, `tests/test_media_metadata.py`, plan + ADR.
- **Edit:** `app.py` (route), `static/js/mediamenu.js` (row + inspect), `static/js/utils.js`
  (`renderMetadataTable`, `copyText`), `static/css/chat.css`,
  `tests/js/utils.test.js`, `tests/test_app_routes.py` (route tests), `CLAUDE.md`.

## Verification

1. `python -m pytest tests/test_media_metadata.py -v`. Use fixtures built in-test,
   without Pillow:
   - a hand-assembled PNG with `tEXt prompt`/`workflow`, an `iTXt`, and a `zTXt`
   - a hand-assembled WEBP with an EXIF chunk in both byte orders
   - `_video_tags` with `subprocess.run` mocked to return the core-`SaveVideo` and VHS
     `comment` shapes, plus the missing-ffprobe path
   - `summarise_prompt_graph` against a small real-looking API graph (CLIPTextEncode ×2,
     KSampler, LoraLoader, linked inputs ignored, 64-bit seed kept as a string)
   - a truncated or corrupt file returns `{}` and does not raise
2. Route tests: auth required, bad filename 400, missing 404, a PNG with metadata gives
   the expected summary, and a JPG gives an empty summary with the seed.
3. `npm run test:js`: `renderMetadataTable` escaping, section order, empty state.
4. `node --check static/js/mediamenu.js static/js/utils.js` (the curly-quote pitfall).
5. `./scripts/test-all`.
6. Manual: `docker-compose up --build -d`. Right-click a t2i PNG, then an H3 MP4, then a
   JPG. Check the table, expand the JSON, and copy a prompt over **plain HTTP**
   (LAN address) to confirm the `execCommand` fallback. Paste the copied API JSON into
   ComfyUI to confirm it loads.
