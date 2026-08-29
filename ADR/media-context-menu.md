# ADR: right-click menu on gallery media (Save · Copy · Copy Seed)

## Context

Two problems met here.

**The hover buttons were full.** `.img-wrap` carries twelve absolutely-positioned
`img-*` buttons (`chat.css`) pinned to every edge and corner — delete, face, upscale,
do-over, i2i, inpaint, crop, i2v, last-frame, edit-meta, macro, re-inpaint. There was no
slot for a thirteenth affordance, while the browser's own right-click menu sat unused
over every image and video in the app.

**Seed reuse was coarse.** `/getseed` read a single process-global "last seed"
(`generation_service._last_seed`) — whatever generated most recently, which is rarely the
image you are looking at, since by the time an image is worth reproducing several more
have usually been generated over the top of it. It was written only for `track_seed=True`
runs (t2i / i2v / t2v), never persisted, and gone on restart.

Nothing recorded **which seed produced which file**. There was no PNG metadata writer (no
Pillow in `requirements.txt`), no sidecar, and no per-filename metadata endpoint — the
"image metadata editor" (`openVideoMetaEditor`) is a client-side prompt/action/audio
editor over `state.imagePrompts` / `state.imageVideoMeta` that stores no seed.

## What was built

Right-click any generated image or video, anywhere in the app, for **Save**, **Copy** and
**Copy seed**, where Copy seed pins *that file's* seed for the next generation, once.

### Server — the per-image seed index

`seed_store.py`, a flat `{filename: "seed-as-string"}` map at `config.SEEDS_FILE`
(`.seeds.json` under `IMAGES_DIR`). Dot-prefixed, and `select_images()` filters on
`MEDIA_EXTS`, so it is never listed by `/api/images` nor swept into an archive.

- **No in-memory cache**, deliberately. `IMAGES_DIR` is the lazily-mounted encrypted
  output volume; a dict loaded before the mount lands would cache the empty stand-in
  directory for the life of the process — the exact failure `@requires_output_storage`
  exists to prevent. The file is small and generations are seconds apart, so it is
  re-read per call. Writes go through `persistence.atomic_write_json` (promoted from
  `_atomic_write_json` for this).
- **Bounded and self-healing**: past `SEED_STORE_MAX` (5000) `_prune` drops entries whose
  image is gone, then oldest-first (dicts preserve insertion order, and `record_seeds`
  re-inserts at the end so that stays accurate). `/api/archive` *moves* files out of
  `IMAGES_DIR` and therefore needs no hook of its own; the two image-delete routes prune
  eagerly anyway via `forget` / `clear`.
- Every write is **best-effort** — losing a seed must never cost the user an image.

`_run_generation_core` now reads `collect_seeds()` **unconditionally** into
`effective_seed` and calls `record_seeds()` immediately after the output files are
renamed (the one point where the seed and the final filenames are both in scope). So
face-detail, upscale, i2i, inpaint, remove and each sequence-run shot all get a recorded
seed — none of which `/getseed` ever covered. `set_last_seed` stays gated on
`track_seed`, leaving `/getseed` semantics unchanged.

`GET /api/image-seed/<filename>` mirrors `/api/last-seed`, including returning the seed as
a **string**: seeds range to `2**64-1` and a JS `Number` would silently round one.
Validated with `secure_filename` + `MEDIA_EXTS`, as `api_delete_image` does. An unknown
file is `{"seed": null}`, not a 404 — the store is an index, not a directory listing.

### Client — the menu

`static/js/mediamenu.js`, a leaf module (imports only `state.js`, `utils.js`, `dom.js`),
initialised once from `chat.js`. **One delegated `contextmenu` listener on `document`**
rather than per-render wiring: the `/images/` URL is already the identity key everywhere,
so a single handler covers chat bubbles, the `/review-all` and sequence-review grids, the
slideshow and the lightbox — including `grids.js` and `slideshow.js`, which render media
without going through `appendChatImage`. The `/images/` prefix check leaves the native
menu over mask/crop editor canvases, `/references` thumbs and `/references-file/`
previews. **Shift+right-click** always falls through to the browser's menu.

Rows branch on `isVideoUrl`: Save image/video; Copy image *or* Copy image/video address;
Copy seed. Save is a transient `<a download>` — `/images/<filename>` is same-origin, so no
`Content-Disposition` was needed.

**Copy image needs a secure context.** `navigator.clipboard.write` exists only under HTTPS
or localhost, and the appliance is normally reached over plain HTTP on a LAN address, so
the row is feature-detected and becomes **Copy image address** (`writeText`) when
unavailable — the menu never shows a row that silently fails. When it *is* available the
blob is transcoded through a canvas to PNG, because Chrome and Firefox accept only
`image/png` in a `ClipboardItem` while the gallery also holds `.webp`/`.jpg`. The
`ClipboardItem` is built from a **promise**, not an awaited blob, so `write()` is called
synchronously inside the click handler (Safari discards the user gesture otherwise);
older Firefox rejects that form and falls back to copying the address.

The seed is fetched when the menu **opens**, so the row states up front whether there is a
seed to take (`Copy seed 1448233…` / `No seed recorded` / `Seed unavailable`) rather than
failing after the click.

### Client — consuming the seed

Nothing new. `state.reuseSeed` was already a one-shot pin, already serialised as `seed` by
the single generation POST in `runGeneration`, already cleared once the response is
accepted, and already validated by `_parse_seed`. Copy seed is a second way to fill the
same field, inheriting `/getseed`'s scope — **t2i / i2v / t2v only** — with
`/getseed-reset` clearing it.

One adjacent bug fixed: `newChat()` reset ~30 fields but not `state.reuseSeed`, so a pin
leaked into the next chat (`restoreSession` cleared it correctly). Copy seed makes pinning
frequent enough to turn that from theoretical into likely.

## Where it lives

| file | change |
|---|---|
| `seed_store.py` | **new** — `record_seeds`, `get_seed`, `forget`, `clear`, `_prune` |
| `config.py` | `SEEDS_FILE`, `SEED_STORE_MAX`, beside the other `IMAGES_DIR` dot-paths |
| `persistence.py` | `_atomic_write_json` → `atomic_write_json` (now cross-module) |
| `generation_service.py` | unconditional `effective_seed`; `record_seeds` after the renames |
| `app.py` | `GET /api/image-seed/<filename>`; prune hooks in both delete routes |
| `static/js/mediamenu.js` | **new** — `initMediaMenu`, `openMediaMenu`, rows, dismissal |
| `static/js/chat.js` | import + `initMediaMenu()` at startup |
| `static/js/utils.js` | `clampMenuPosition` (the only DOM-free, testable part) |
| `static/js/commands.js` | `newChat` resets `reuseSeed`; `/getseed` help cross-reference |
| `static/css/chat.css` | `.media-menu` / `.media-menu-item`, `z-index: 10000` |

## Decisions

- **Delegated listener, not per-render wiring.** `grids.js` and `slideshow.js` render media
  outside `appendChatImage` and the `img-*` classes are already duplicated across three
  call sites; this avoided a fourth.
- **Shift+right-click escapes to the native menu.** Overriding a `<video>`'s context menu
  costs playback-speed and picture-in-picture; this is the cheap standard hatch.
- **Server-side store, not client session state.** A `state.imageSeeds` map fed by the SSE
  `done` event would have ridden the existing `imagePrompts` pattern with no new storage,
  but Copy seed would then only work on images in the current chat — greyed out across
  `/review-all`. A right-click menu implies it works on whatever you right-clicked.
- **Read per call, no cache.** The lazy encrypted-output mount makes a warm cache a
  correctness hazard, not an optimisation.
- **Record for every job kind**, not just the `track_seed` three: the file exists either
  way, so the extra coverage is free.
- **Archived images lose their seed.** The map is a convenience index, not provenance, and
  the prune keeps it honest.
- **Not retroactive.** Pre-existing images show `No seed recorded`; there is nowhere to
  recover it from.
- **iOS long-press is out of scope.** Desktop and Android Chrome both fire `contextmenu`
  (Android on long-press), so they get the menu free. iOS Safari does not, and a synthetic
  touch-hold would collide with the pinch/swipe handlers in `lightbox.js` and the
  drag-sort in `grids.js`.

## Tests

- `tests/test_seed_store.py` — roundtrip, string encoding of a full 64-bit seed, shared
  seed across one job's outputs, corrupt/non-dict file recovery, forget/clear, and the
  three prune behaviours (orphans kept under the cap, missing-images dropped first,
  oldest-first thereafter, re-record refreshing position).
- `tests/test_generation_service.py::CoreSeedRecordingTests` — drives the **real**
  `_run_generation_core` against a stub ComfyUI server (it is mocked out everywhere else
  in that file), covering the randomize and pin paths, that recording happens without
  `track_seed` while the `/getseed` global stays untouched, that `track_seed` still
  updates it, that a seedless workflow records nothing, and that a failed seed write does
  not fail the generation.
- `tests/test_app_routes.py::TestImageSeed` — the endpoint's seed/null/validation cases
  and both delete-prune hooks.
- `tests/js/utils.test.js` — `clampMenuPosition` viewport clamping.

`mediamenu.js` itself is untestable under the DOM-free jest harness, like `chat.js` and
`commands.js`; it was verified by hand per the plan's checklist.
