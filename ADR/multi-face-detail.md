# Multi-face face-detail (👥 Multiple faces…)

## Context

Face-detail used to work on the whole image. The face-detailer workflow finds faces by itself, and the app sends only the image, a prompt and a denoise value. With two or more characters in one image, every face therefore gets the same prompt and the same LoRA. There was no way to give each face its own identity.

## Decision

The 👤 **Face detail** dialog has a third button, **👥 Multiple faces…**. It opens a full-screen editor where the user paints up to four faces and gives each one its own prompt. The server then, for each face:

1. cuts a square out of the image around that face,
2. face-details the square on its own,
3. pastes the result back over the original.

The result arrives in the same before/after comparison slider as a normal face-detail.

### Client

- **Entry point.** `showI2IDialog` (`chat.js`) takes an optional `extraAction`. Clicking it resolves the dialog with `extra: true`, and the 👤 caller then opens `openMultiFaceEditor` (`facemulti.js`). The dialog's prompt and denoise carry over as the editor's defaults.
- **Painting.** Each face has its own canvas layer in its own colour: 1 red, 2 green, 3 blue, 4 yellow. The layers are shown at 50% opacity under a transparent input canvas. Face buttons 1–4, or keys `1`–`4`, choose which layer the brush paints on.
- **Prompt box.** A single prompt box shows the active face's prompt.
- **Other controls.**
  - *Clear face* wipes the active face's mask.
  - *Reset* wipes every mask and sets all four prompts back to the dialog's prompt.
  - The margin slider runs from 0 to 100 px and defaults to 20.
  - `[`/`]` and Escape behave as they do in the mask editor.
- **Apply.**
  1. `faceMultiPayload` (`utils.js`) drops faces with no paint. A painted face with no prompt is flagged and nothing is sent.
  2. Each painted face's mask is binarised at the image's natural size and uploaded through the existing `/api/upload-mask`.
  3. `runFaceDetailMulti` → `runGeneration({faceMulti, sliderReplace})`.
  4. The result goes through the normal slider path, including the 🩹 composite button.
- **Memory.**
  - `state.faceMultiPrompts` (four entries, each a string or null) and `state.faceMultiMargin` are stored **per chat**.
  - Both are saved with the session, included in the `/settings-save` stack, reset by `newChat`, and shown by `/settings`.
  - `restoreSession` always normalises them, so a chat saved before this feature does not pick up the previous chat's prompts.
  - A prompt is remembered as it is typed, so Cancel does not lose it.
- **Shared brush code.** The brush code that `openMaskEditor` and `openCompositeEditor` each had a copy of is now exported helpers in `editors.js`: `sizeCanvasesToImage`, `brushStroke`, `createBrushCursor`, `binaryMaskCanvas`, `canvasHasPaint` and `uploadMask`. Moving it fixed a HiDPI bug: the cursor circle was drawn at `radius / dpr`, but strokes are painted in CSS px, so on HiDPI screens the cursor looked smaller than the brush.

### Server

- **Route.** `POST /api/face-detail-multi` (`app.py`) takes `{image, workflow?, denoise?, margin?, faces: [{mask, prompt}]}`.
  - It validates 1–`FACE_MULTI_MAX_FACES` (4) faces, a prompt per face that is still non-empty once LoRA tags are removed, and a margin of 0–`FACE_MULTI_MAX_MARGIN` (200). It also checks the source image, the face-detailer workflow, the denoise value and storage readiness.
  - The single-use mask tokens are resolved **last**, so a rejected request can be fixed and sent again with the same tokens. If one token fails, the masks already resolved are deleted.
- **Image maths.** `face_multi.py` is Pillow only and never imports the app.
  - `mask_bbox`: bounding box of the mask, thresholded at 128.
  - `square_box`:
    - The side is `max(w, h) + 2 × margin`, centred on the mask's bounding box.
    - It is capped at the image's shorter side.
    - At an image edge it is *shifted* back inside, not shrunk.
  - `feather_alpha`:
    - Fully opaque over the bounding box, then a linear ramp to 0 across the margin band, built from concentric rectangles.
    - Each side ramps across its own gap, so a square shifted against an image edge stays hard on that side and still fades on the others.
    - With margin 0 the paste is hard.
  - `prepare_crop`: upscales crops smaller than 1024 px to 1024 (Lanczos). Larger crops are sent at their native size.
  - `paste_back`: scales the result back to the box size and pastes it through the alpha mask.
- **Job.** `run_face_detail_multi` and `start_face_detail_multi_job` (`generation_service.py`) mirror the face-detail-super job. It is an `image`-kind job, so `/jobs`, ✕ cancel and the progress bar all work. For each face it:
  1. checks for cancel,
  2. writes the crop to `FACE_MULTI_INPUTS_DIR` (`.face-multi-inputs/`, dot-prefixed so galleries skip it),
  3. runs `_run_generation_core` with that face's prompt and LoRAs over the crop,
  4. pastes the result onto the working image.

  After the last face:
  - It saves `<ts>_facemulti_<uuid8>.png`, copies the source image's mtime onto it, and sends `done` with that one image.
  - Whatever the outcome, it deletes the per-face ComfyUI outputs (including their seed entries), the crops and the masks.

### Dependency

Pillow is added to `requirements.txt` (prebuilt wheels exist for amd64 and arm64). Until now the server had no image library, and all compositing was done on a canvas in the browser. That approach was rejected here because a browser-side run would stop if the tab closed, and the result could not be unit-tested in Python.

## Consequences

- **Faces need prompts.** Every face needs its own prompt, normally with its own `<lora:…>`. The 👤 button still requires a derivable or set face-detail prompt before the dialog opens.
- **No `/getseed` for the composite.** The result has no single seed, so no entry is written to `seed_store`.
- **Overlapping squares.** Where two squares overlap, the later face is pasted over the earlier one.
- **What the workflow sees.** The face-detailer workflow receives a square crop that is roughly one face. A workflow that detects faces itself (FaceDetailer with a bbox detector) still works; it just finds one face.
