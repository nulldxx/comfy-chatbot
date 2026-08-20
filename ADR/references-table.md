# ADR: `/references` multi-slot reference-asset table

## Context

The app previously supported a single identity-reference image, pinned with
`/i2v-set-ref-image` into `state.refImageUrl` and fed to the sole `<REFERENCE_IMAGE>`
placeholder used by the LTX 2.3 face-ID workflows. MiniMax H3 (R2V) needs many more
reference inputs — **3 reference images, 1 reference video, 1 reference audio for the
video, and 1 further reference audio** — including media types (video, audio) the app
had no way to feed into a workflow. We replaced the single pin command with a
`/references` table and a placeholder scheme a MiniMax workflow can consume, while
keeping the LTX case working (image 1 only).

Scope: UI + state + API plumbing + tag scheme + node stripping for unfilled slots. The
MiniMax workflow JSON itself is authored separately in `~/comfy-workflows/`.

## Tag scheme

All string placeholders (quoted filename slots), filled only when present in the
template:

| Placeholder | Meaning | Unfilled |
|---|---|---|
| `<REFERENCE_IMAGE>` | Image slot 1 **with fallback** (LTX identity ref) | fall back to `<INPUT_IMAGE>`, else error |
| `<REFERENCE_IMAGE_1>` / `<REFERENCE_IMAGE_2>` / `<REFERENCE_IMAGE_3>` | MiniMax images 1/2/3 (slot 1 = same source as `<REFERENCE_IMAGE>`, no fallback) | sentinel + strip loader node |
| `<REFERENCE_VIDEO>` | Reference video | sentinel + strip |
| `<REFERENCE_VIDEO_AUDIO>` | Audio paired with the video | sentinel + strip |
| `<REFERENCE_AUDIO>` | Further reference audio | sentinel + strip |

## Implementation

**Backend**
- `config.py` — `REFERENCES_DIR` (`.references/` under `IMAGES_DIR`, dot-prefixed →
  excluded from galleries, encrypted, **persistent**); `AUDIO_EXTS`.
- `app.py` — `POST /api/upload-reference` (multipart `file` + `kind`, → persistent
  `/references-file/<name>` URL) and `GET /references-file/<name>`; `_resolve_references()`
  turns the client `references` object into `input_reference_images` /
  `input_reference_video` / `input_reference_video_audio` / `input_reference_audio`
  kwargs; both video endpoints forward them (`**ref_kwargs`).
- `image_store.py` — `resolve_reference(url, allowed_exts)` accepts `/images/` and
  `/references-file/` URLs, validating the extension class per slot.
- `ComfyServer.py` — `upload_media()` generalises `upload_image` (content-type by
  extension; ComfyUI's `/upload/image` routes video/audio into the input dir too).
- `generation_service.py` — `_run_generation_core` fills each `<REFERENCE_*>` token that
  is in the template, uploading via `upload_media` and caching per source path; image 1
  keeps the `<INPUT_IMAGE>` fallback. Unfilled optional slots get a `reference_sentinel()`
  filename, then `strip_reference_nodes()` removes their loaders after JSON parse.
- `workflow.py` — `reference_sentinel(token)` and `strip_reference_nodes(workflow,
  sentinels)`: delete each sentinel-holding loader node and **drop** any consumer input
  referencing it (absent optional input — no passthrough, unlike `strip_lora_nodes`).

**Frontend**
- `state.js` — `references` object with `newReferences()` / `cloneReferences()` factories.
- `commands.js` — `/references` renders an inline-bubble table (one row per slot) with
  drop-zones, thumbnails/audio chips, a ✕ clear, and a click-to-browse file input.
  Registered in the `/help` table and `SETTINGS_MENU`; the old two commands removed.
- `chat.js` — `referencesForRun()` builds the payload (image 1 suppressed when it equals
  the triggered image); `appendChatImage` makes chat media draggable via the
  `application/x-comfy-url` dataTransfer type (`COMFY_URL_DND_TYPE` in `utils.js`);
  session save/restore + newChat carry `references`.
- `autocomplete.js` — the two old entries replaced by `/references`.

## Consequences

- Old sessions' `refImageUrl` migrates into `images[0]`; `/settings-save` now snapshots
  references (the old field never was).
- Reference video/audio are the first non-image inputs fed to a workflow; they rely on
  ComfyUI accepting the file at `/upload/image` and the authored workflow's loader nodes
  reading from the input dir — verify when authoring the MiniMax graph.
- `e2fsck -fy`-style caveats and encryption apply to `REFERENCES_DIR` as it lives on the
  output volume.

## Tests

`tests/test_workflow.py` (`strip_reference_nodes`), `tests/test_image_store.py`
(`resolve_reference`), `tests/test_generation_service.py` (multi-slot fill + strip, and
the updated LTX fallback), `tests/test_app_routes.py` (`/api/upload-reference`,
`/references-file`, and the `references` payload on `/api/text2video`).
