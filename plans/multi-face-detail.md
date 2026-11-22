# Multi-face face-detail (👥 Multiple faces…)

## Context
Face-detail today works on the whole image. The workflow finds faces by itself, and the app sends only the image, a prompt and a denoise value. With several characters in one image there is no way to give each face its own prompt or LoRA. This feature lets the user paint up to 4 faces and give each its own prompt. The server then:
- cuts a square around each face,
- face-details each square on its own,
- pastes the squares back over the original with feathered edges.

The result is shown in the existing A/B comparison slider.

Decisions made with the user:
- Processing runs **on the server**, as a new job using **Pillow**.
- Squares are pasted back **feathered across the margin**.
- Crops are **upscaled to 1024** before detailing.
- Per-face prompts are remembered **per chat**.

## UX
- **Entry point.** `showI2IDialog` (`static/js/chat.js:716`) gets an optional `extraAction: {label, onClick}` button, placed next to Cancel/OK. Only the 👤 face caller (`chat.js:~1239`) passes it, as **👥 Multiple faces…**. Clicking it closes the dialog and calls `openMultiFaceEditor(url, wrap, {prompt, denoise})`.
- **Editor layout.** A full-screen overlay laid out like the mask editor (`openMaskEditor`, `static/js/editors.js:9`). The image sits under **4 mask canvases**, one per face, each with its own colour at about 50% opacity: 1 red, 2 green, 3 blue, 4 yellow.
- **Toolbar:**
  - **Face buttons 1–4**, coloured to match, with the active face highlighted. Keys `1`–`4` do the same thing.
  - **One prompt textarea** that shows and edits the active face's prompt. A face with no remembered prompt starts with the prompt from the face-detail dialog.
  - **Margin slider**: 0–100 px, default 20, also remembered.
  - **Denoise slider**: starts at the dialog's value.
  - **Clear face**: wipes the active face's mask.
  - **Reset**: wipes all 4 masks and sets all 4 prompts back to the dialog prompt.
  - **Cancel** and **Apply**.
  - Brush keys `[`/`]` and Escape behave as they do in the mask editor.
- **Apply:**
  1. Every face with mask pixels must have a prompt. If one is empty, its button and the prompt box are flagged and nothing is sent.
  2. Each painted face's mask is binarised at the image's natural size, using the existing export code.
  3. Each mask goes to the existing `/api/upload-mask`, which returns a token.
  4. Run `runGeneration(..., { faceMulti: {...}, sliderReplace: wrap })`, so the result lands in `buildComparisonSlider` with no new code.

## Implementation

### Shared editor helpers (`static/js/editors.js`)
Move the code that `openMaskEditor` and `openCompositeEditor` both duplicate into small shared helpers:
- canvas sizing (`syncCanvasSize`)
- `stroke`
- the brush cursor
- mask binarise + export to b64

The new editor uses these helpers instead of copying the code a third time. While doing this, fix the cursor radius on high-DPI screens. The cursor currently uses `brushRadius / dpr`, but strokes are painted at `brushRadius` CSS px, so the cursor looks smaller than the brush.

### New `static/js/facemulti.js`
- Contains `openMultiFaceEditor`.
- Reads and writes `state.faceMultiPrompts` and `state.faceMultiMargin`, then calls `scheduleRecordSave()`.
- Pure helpers go in `utils.js` so they can be tested:
  - `FACE_MULTI_COLOURS`
  - `faceMultiPayload(faces)`: drops faces with no mask and validates prompts.

### State (`static/js/state.js`)
- New fields: `faceMultiPrompts: [null, null, null, null]` and `faceMultiMargin: 20`.
- Wire them into:
  - `doRecordSave` and `restoreSession` (`chat.js:1530`/`1586`)
  - `/settings-save` and `/settings-restore` (`commands.js:~3459`/`3503`)
  - the `newChat` reset
  - the `/settings` display

### `runGeneration` (`chat.js:2355`)
- New `opts.faceMulti` (`{image, workflow, denoiseOverride, margin, faces:[{mask, prompt}]}`), which posts to `/api/face-detail-multi`.
- It counts as a `job`, so it inherits the existing `sliderReplace` path and the 🩹 composite button.

### Image maths (`face_multi.py`, new, Pillow only, no app imports)
- `mask_bbox(mask_img) -> (l,t,r,b) | None`: wraps `Image.getbbox()` on the L-mode mask.
- `square_box(bbox, margin, W, H)`:
  - `side = max(w,h) + 2*margin`, centred on the bbox, capped at `min(W,H)`.
  - Shifted so it stays inside the image.
- `feather_alpha(side, inner_box)`:
  - An L mask, fully opaque over the face box.
  - Ramps linearly to 0 across the margin band, built from concentric rectangle outlines.
  - Margin 0 gives a hard paste.
- `prepare_crop(img, box, target=1024)`: crops, then upscales with Lanczos to 1024 if the crop is smaller (native size if larger). Returns the image and its original side.
- `paste_back(base, processed, box, alpha)`: resizes `processed` back to the box size and pastes it with the alpha mask.

### Job (`generation_service.py`)
New `run_face_detail_multi` plus `start_face_detail_multi_job`, modelled on `run_face_detail_super` and `start_face_detail_super_job` (`:784`/`:829`). The job is an `image` kind, so `/jobs` and cancel work. For each face:
1. Check for cancel.
2. Send `progress` "Face i/N…".
3. Write the crop to a temporary file under a new dot-dir, `IMAGES_DIR/.face-multi-inputs`.
4. `_run_generation_core(..., workflow_dir=COMFY_FACEDETAILER_DIR, input_image=crop, cleanup_input_image=True, denoise=...)` using that face's own prompt and LoRAs.
5. Load the result and paste it onto the working image.
6. Remove the intermediate output with `_discard_gallery_file` and forget its seed.

Then:
- Save the final image as `IMAGES_DIR/{ts}_facemulti_{uuid8}.png` and copy the source image's mtime onto it.
- Send `done` with `images=[final]`.
- Always unlink the mask files.
- On error or cancel, also remove any intermediates.

### Route (`app.py`): `POST /api/face-detail-multi`
- `@login_required` and `@requires_output_storage`.
- Body: `{image, workflow, denoise, margin, faces:[{mask, prompt}], server, server_os}`.
- Validation:
  - `faces` must have 1–4 entries.
  - `margin` must be an int from 0 to 200.
  - For each prompt, `parse_loras_from_prompt` must leave it non-empty.
  - Each mask is resolved with `resolve_mask(token, user)`.
  - Uses the existing `resolve_input_image`, `resolve_workflow(..., list_facedetailer_workflows(), ...)`, `_parse_denoise` and `output_storage_error`.
- Any invalid input returns 400 before the job starts.

### Dependencies and packaging
- Add `Pillow` to `requirements.txt`. Prebuilt wheels exist for amd64 and arm64 slim images.
- Add `COPY face_multi.py .` to the Dockerfile, which lists each module by name.
- Add `PIL` to `tests/test_imports.py`.
- Add `FACE_MULTI_INPUTS_DIR` to `config.py`.

### Docs, as CLAUDE.md requires
- Commit `plans/multi-face-detail.md` before starting implementation.
- Afterwards, write `ADR/multi-face-detail.md` and add a CLAUDE.md section on how it works: the square geometry, the feather, the 1024 upscale, and per-chat prompts.

## Verification
- `python -m unittest discover tests/`, with new tests for:
  - **`tests/test_face_multi.py`**:
    - bbox of empty and non-empty masks
    - `square_box` clamping at edges and corners, and when the face is bigger than the image
    - feather values at the centre, the margin band and the edge
    - the round trip of `prepare_crop`/`paste_back` (a solid-colour processed crop changes only pixels inside the box)
  - **Route tests in `tests/test_app_routes.py`**: 400 cases (no faces, 5 faces, empty prompt, bad token, bad margin) and the happy path with the job starter mocked.
  - **`tests/test_generation_service.py`**: `_run_generation_core` mocked to return a solid-colour image. Check that the final file exists, the intermediates are deleted, `done` carries 1 image, and cancel between faces works.
- `npm run test:js` for `faceMultiPayload`.
- `node --check` on every edited JS file (the curly-quote pitfall).
- `./scripts/test-all`. The Docker build proves Pillow installs and `face_multi.py` is copied in.
- Manual check with the `run` skill or on a live server:
  1. Open 👤, then 👥, on an image with 2 people.
  2. Paint faces 1 and 2 and give them different LoRA prompts.
  3. Apply and confirm the comparison slider shows both faces changed with no visible square seams.
  4. Reopen the editor and confirm the prompts and margin are remembered.
  5. Reload the chat and confirm they are still remembered.
- Release with `push-to-portainer` and ask for confirmation before the Portainer redeploy.
