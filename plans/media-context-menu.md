# Plan: right-click menu on gallery media (Save · Copy · Copy Seed)

## Context

Two separate frustrations meet here.

**The hover buttons are full.** `.img-wrap` already carries twelve absolutely-positioned
`img-*` buttons (`chat.css:269-484`) pinned to every edge and corner — delete, face,
upscale, do-over, i2i, inpaint, crop, i2v, last-frame, edit-meta, macro, re-inpaint.
There is no free slot for a thirteenth affordance, and the browser's own right-click menu
sits unused over every image and video in the app.

**Seed reuse is coarse.** `/getseed` exists and works, but it reads a single
process-global "last seed" (`generation_service._last_seed`) — the seed of whatever
generated most recently, which is rarely the image you are actually looking at. By the
time you see an image worth reproducing, three more have usually been generated over the
top of it. Worse, `_last_seed` is only written when `track_seed=True` (t2i / i2v / t2v),
is never persisted, and is gone on restart.

Nothing anywhere records **which seed produced which file**. There is no PNG metadata
writer (no Pillow in `requirements.txt`), no sidecar, and no per-filename metadata
endpoint. The "image metadata editor" (`openVideoMetaEditor`, `chat.js:479`) is a
client-side prompt/action/audio editor over `state.imagePrompts` / `state.imageVideoMeta`
— it stores no seed and fetches nothing.

The outcome we want: right-click any generated image or video, anywhere in the app, and
get **Save**, **Copy**, and **Copy seed** — where Copy seed pins *that specific file's*
seed for the next generation, exactly once.

## Approach

Three independent pieces, only the third of which is novel.

### 1. Server: record the seed per output file

A new `seed_store.py` (sibling of `auth_store.py` / `image_store.py`) backed by
`IMAGES_DIR / '.seeds.json'` — a flat `{filename: "seed-as-string"}` map. Dot-prefixed so
the workflow/gallery globs skip it, and `select_images` (`image_store.py:136`) filters on
`MEDIA_EXTS`, so it is never listed by `/api/images` nor swept into an archive.

- `record_seeds(filenames, seed)` / `get_seed(filename)`, both under a module lock,
  writing via `persistence._atomic_write_json` (temp file + `os.replace`, already used
  for every session doc).
- **No in-memory cache.** `IMAGES_DIR` is the lazily-mounted encrypted output volume; a
  dict loaded before the mount lands would cache the empty stand-in directory forever —
  precisely the bug the `@requires_output_storage` decorator was added to fix. Read the
  file per call instead: it is small, and generations are seconds apart.
- **Bounded**: when the map exceeds `SEED_STORE_MAX` entries, drop entries whose file no
  longer exists in `IMAGES_DIR`, then, if still over, drop oldest-first (dict insertion
  order). Self-healing, so archiving (which *moves* files out) needs no hook. Belt and
  braces: `DELETE /api/images/<filename>` (`app.py:2131`) drops that one entry and
  `DELETE /api/images` (`app.py:2147`) clears the map.

Write site: `_run_generation_core` (`generation_service.py:299`). The effective seed is
already computed at lines 561-574 and the destination filenames are minted at 606-618 —
same function scope. Hoist `collect_seeds(workflow)` out of the `if track_seed:` guard so
`effective_seed` is available unconditionally, keep `set_last_seed` gated on `track_seed`
(so `/getseed` semantics are unchanged), and call `record_seeds(names, effective_seed)`
after the renames. This means **every** job kind gets a recorded seed — face-detail,
upscale, i2i, inpaint, remove and each sequence-run shot included — which `/getseed`
never covered. Workflows with no `seed`/`noise_seed` input yield `[]` and record nothing.

New endpoint, mirroring `/api/last-seed` (`app.py:961`) including its string-encoding
rationale (a 64-bit seed loses precision as a JS `Number`):

```python
@app.route("/api/image-seed/<filename>")
@login_required
@requires_output_storage
def api_image_seed(filename): ...   # -> {"seed": "1448..."} or {"seed": None}
```

Validate with `secure_filename` + `MEDIA_EXTS` exactly as `api_delete_image` does.

### 2. Client: the context menu

New module `static/js/mediamenu.js` (leaf imports only — `state.js`, `utils.js`, `dom.js`
— so no cycle), exporting `initMediaMenu()`, called once from `chat.js`. Keeping it out
of the already-100 KB `chat.js` matches how `lightbox.js` / `slideshow.js` / `grids.js`
are split.

One **delegated `contextmenu` listener on `document`**:

```js
document.addEventListener('contextmenu', e => {
  if (e.shiftKey) return;                        // escape hatch to the native menu
  const el = e.target.closest('img, video');
  if (!el) return;
  const url = el.getAttribute('src');
  if (!url || !url.startsWith('/images/')) return;
  e.preventDefault();
  openMediaMenu(url, e.clientX, e.clientY);
});
```

Delegation is what makes this cheap: the URL is already the identity key everywhere in
this codebase, so one handler covers chat bubbles, `/review-all` and sequence-review grids
(`grids.js`), the slideshow, and the lightbox — including the two renderers that bypass
`appendChatImage` entirely. Gating on the `/images/` prefix automatically excludes mask
and crop editor canvases, `/references` table thumbs and uploaded `/references-file/`
previews, which should keep the native menu.

Rows, branching on `isVideoUrl(url)` (`utils.js:18`):

| | image | video |
|---|---|---|
| Save | `Save image` | `Save video` |
| Copy | `Copy image` *or* `Copy image address` | `Copy video address` |
| Seed | `Copy seed` | `Copy seed` |

- **Save** — a transient `<a href=url download=filename>` clicked programmatically. Works
  as-is: `/images/<filename>` (`app.py:1866`) is same-origin, so no `Content-Disposition`
  is needed. Same `<a download>` idiom as `/last-sent` (`commands.js:2616`).
- **Copy image** — shown only when `window.isSecureContext && navigator.clipboard?.write`.
  The live deployment publishes plain HTTP on `:5000`, so on a LAN address this API is
  simply absent and the row becomes **Copy image address** (`writeText` of the absolute
  URL), which always works. When it *is* available: `fetch` → blob → draw to a canvas →
  `toBlob('image/png')` → `new ClipboardItem({'image/png': blob})`. The canvas transcode
  is not optional — Chrome and Firefox accept only `image/png` in a `ClipboardItem`, and
  the gallery holds `.webp`/`.jpg` too (`IMAGE_EXTS`, `config.py:189`). Video is
  address-only, as you suggested.
- **Copy seed** — `GET /api/image-seed/<filename>` fires when the menu *opens*; the row
  starts disabled reading `Copy seed…` and resolves either to `Copy seed 1448233…` or to
  a disabled `No seed recorded`. On click it sets `state.reuseSeed`, closes the menu and
  posts the same confirmation bubble `/getseed` does.

Dismissal follows the `editors.js` overlay idiom (`const dismiss = () => ...` plus a
document `keydown` for Escape): outside click, Escape, scroll, `window.blur`, or another
right-click. Positioned `position:fixed` at the pointer, clamped into the viewport, at
`z-index: 10000` so it sits above the lightbox (9999) — right-clicking a lightboxed image
must work.

### 3. Client: consuming the seed

**Nothing new is needed.** `state.reuseSeed` (`state.js:192`) is already a one-shot pin,
already serialised as `seed` by the single generation POST in `runGeneration`
(`chat.js:1947`, seed lines 2004-2043), already cleared after the response is accepted,
and already validated server-side by `_parse_seed` (`app.py:1009`). "Copy seed" is just a
second way to fill the same field — it reuses `/getseed`'s entire pipeline and inherits
its scope: **t2i / i2v / t2v only** (`primaryGen = !job || image2video || text2video`),
and `/getseed-reset` clears it.

One adjacent bug to fix while here: `newChat()` (`commands.js:1018`) resets ~30 fields but
**not** `state.reuseSeed`, so a pending pin leaks into the next chat. `restoreSession`
(`chat.js:1392`) clears it correctly. Copy-seed makes pinning far more frequent, so this
goes from theoretical to likely.

## Work

1. **`seed_store.py`** (new) — `record_seeds`, `get_seed`, `forget`, `clear`, the prune,
   `SEED_STORE_MAX`. Reuses `persistence._atomic_write_json`.
2. **`config.py`** — `SEEDS_FILE = IMAGES_DIR / '.seeds.json'`, beside the existing
   `MASKS_DIR` / `INPAINT_INPUTS_DIR` / `REFERENCES_DIR` dot-path block (lines 119-137).
3. **`generation_service.py`** — hoist `collect_seeds` out of the `track_seed` guard in
   `_run_generation_core`; `record_seeds(...)` after the dest renames (~line 618).
4. **`app.py`** — `GET /api/image-seed/<filename>`; prune hooks in the two image-delete
   routes.
5. **`static/js/mediamenu.js`** (new) — `initMediaMenu()`, `openMediaMenu(url, x, y)`, the
   three row builders, dismissal wiring.
6. **`static/js/chat.js`** — import and call `initMediaMenu()` at startup.
7. **`static/js/utils.js`** — `clampMenuPosition(x, y, w, h, vw, vh)`, a pure helper (the
   only part of the menu that is unit-testable under the DOM-free jest harness).
8. **`static/js/commands.js`** — reset `state.reuseSeed` in `newChat()`; cross-reference
   the menu from the `/getseed` `/help` entry (~line 2250).
9. **`static/css/chat.css`** — a `.media-menu` / `.media-menu-item` component in the house
   dark palette (`#0f172a` ground, `#334155` border, `#7c3aed` hover accent). None exists.
10. **Tests** — `tests/test_seed_store.py` (record/get/atomic write/prune-missing/prune-
    oversize); `/api/image-seed` cases in `tests/test_app_routes.py` alongside
    `TestLastSeed`; `clampMenuPosition` in `tests/js/utils.test.js`.
11. **Docs** — a CLAUDE.md section for the menu and the seed store; an ADR once landed,
    per the repo convention.

## Decisions

- **Delegated listener, not per-render wiring.** `grids.js` and `slideshow.js` render
  media outside `appendChatImage`; the `img-*` button classes are already duplicated
  across three call sites, and this avoids adding a fourth duplication.
- **Shift+right-click falls through to the native menu.** Overriding the video context
  menu costs you Chrome's playback-speed / picture-in-picture entries; this is the cheap
  standard escape hatch.
- **Read `.seeds.json` per call, no cache.** Deliberate: the lazy encrypted-output mount
  makes a warm cache a correctness hazard, not an optimisation.
- **Record for every job kind**, not just the `track_seed` three. The file exists, so the
  seed that made it may as well be recorded; the extra coverage is free.
- **Archived images lose their seed.** `/api/archive` moves files off `IMAGES_DIR` and the
  seed map is not archived with them. Accepted — the map is a convenience index, not
  provenance, and the prune keeps it honest.
- **Not retroactive.** Images generated before this ships have no recorded seed and will
  show `No seed recorded`. There is nowhere to recover it from.
- **iOS long-press is out of scope.** Desktop browsers and Android Chrome both fire
  `contextmenu` (Android on long-press), so they get the menu for free. iOS Safari does
  not, and a synthetic touch-hold would collide with the existing pinch/swipe handlers in
  `lightbox.js` and the drag-sort in `grids.js`. Worth a follow-up, not worth destabilising
  those gestures here.

## Verification

1. `./scripts/test-all` (Python unit + import + Docker container) and `npm run test:js`
   — the JS suite is **not** part of `test-all` (CLAUDE.md).
2. `node --check static/js/mediamenu.js && node --check static/js/chat.js` — the repo's
   documented curly-quote hazard after any JS edit.
3. `docker-compose up --build -d`, log in, then by hand:
   - Generate an image. Right-click it → menu appears, native menu suppressed.
     **Save image** downloads with the gallery filename. **Copy image address** (or
     **Copy image** over localhost/HTTPS) pastes correctly.
   - **Copy seed** shows the seed. Cross-check it against `/last-sent` — the seed in the
     submitted workflow JSON must match.
   - Generate again with no prompt change: the new image must be pixel-identical, and the
     progress log must read `Reusing seed <n>` rather than `Randomized seed values`.
   - Generate a third time: back to `Randomized seed values` (one-shot consumed), and a
     different image.
   - `/getseed-reset` after a Copy seed clears the pin.
4. Right-click coverage: a video in chat (Save + address only, no Copy video), a thumb in
   `/review-all`, an image inside the lightbox (menu must draw above it), and a slideshow
   frame. Right-click a `/references` table thumb and the mask editor canvas → **native**
   menu, unchanged.
5. `cat ~/comfy-workflows/../.seeds.json` on the host (under `IMAGES_DIR`) — entries
   present; confirm `/api/images` and a `/archive` of the session do not include it.
6. Restart the container, log in, right-click an older image → seed still there (the point
   of storing it server-side rather than in `state`).
7. Pin a seed, then `/new` → the pin must be gone (the `newChat` fix).
