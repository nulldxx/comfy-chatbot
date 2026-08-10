# ADR: Text-to-video mode (`/t2v`)

**Status:** Implemented.
**Scope decided before build:** a dedicated `text2video/` workflow family (not graph
surgery on the i2v templates), and a persistent on/off toggle (not a one-shot command).

## Problem

Every plain chat prompt went to the text-to-image workflow. Video was reachable only
*from an existing image* — the 🎬 button, `/i2v`, `/i2v <N>` — because
`/api/image2video` requires an `image` and every i2v template carries an `<INPUT_IMAGE>`
`LoadImage` node.

Newer video models are multi-modal and generate perfectly well from a prompt alone
(MiniMax H3; the LTX 2.3 face-ID `ref_t2v` graph already in the tree has no first frame
at all). Reaching them meant generating a still image purely to throw it away.

## Decision

Add `/t2v`, a mode toggle. While it is on, anything typed into the chat is generated as
a video by the selected **text2video** workflow. Typing `/t2v` again turns it off.

### Why a separate `text2video/` dir rather than stripping the i2v graph

The alternative was to reuse the current `/i2v-workflow` selection and delete the
`<INPUT_IMAGE>` `LoadImage` chain from the graph at submit time, in the style of
`strip_last_frame_guide()`. Rejected because:

- **The stop condition can't be inferred.** Walking forward from the `LoadImage` you
  must distinguish a pure image transform that has to be *deleted* (`ResizeImageMaskNode`)
  from the real consumer whose input key should merely be *dropped*
  (`MiniMaxH3ImageToVideo.first_frame`). Nothing in the API-format JSON says which is
  which — that lives in ComfyUI's `/object_info` schema, and querying it would put a live
  HTTP dependency in the generation path.
- **It isn't universally valid.** LTX's `LTXVImgToVideoInplace` *produces* the latent
  from its image; removing it doesn't yield a working t2v graph, it yields a broken one.
  A heuristic strip would silently mangle those templates.
- **A separate dir is the shape the codebase already has** — seven workflow families,
  each a subdir + env default + `list_*_workflows()` + a picker command. An eighth costs
  almost nothing and the "does this workflow support t2v?" question becomes "is it in
  `text2video/`?" instead of a guess made at runtime.

The cost — a second template per video model — is real but small, and it is a one-time
export from the ComfyUI editor where the graph can actually be verified.

### Why the mode is a toggle

`/t2v <prompt>` (one-shot) would have avoided any hidden state, but the point of the
feature is to *work* in text-to-video for a while. A toggle plus a visible header badge
(`🎬 t2v`, and the header shows the t2v workflow name while active) keeps the state
discoverable without retyping the command each prompt.

## How it was implemented

**Backend**

- `config.py` — `COMFY_TEXT2VIDEO_DIR` (`text2video/` under `COMFY_WORKFLOW_DIR`) and
  `COMFY_TEXT2VIDEO_WORKFLOW`, via the existing `_norm_workflow_default`.
- `catalogue.py` — `list_text2video_workflows()`, reusing `list_workflow_names`.
- `app.py` — `/api/text2video-workflows` (listing) and `/api/text2video` (generation).
  The latter is `api_image2video` minus the image plumbing: prompt required, no `image`,
  no `last_frame`, optional `ref_image`, `workflow_dir=COMFY_TEXT2VIDEO_DIR`. The
  allow-list (`resolve_workflow`) and traversal guard are unchanged and reused.
- **`generation_service.py` needed no change for the happy path.** With `input_image=None`
  the `INPUT_IMAGE` mapping key is simply never set, and a template with no
  `<INPUT_IMAGE>` token passes the unfilled-placeholder check. `start_generation_job`'s
  `is_video` classification already keys off the video kwargs, so `/jobs` labels these
  `kind: "video"` for free.
- One robustness fix: `<REFERENCE_IMAGE>`'s fallback was `mapping.get("INPUT_IMAGE", "")`,
  which in a t2v run becomes `""` and fails deep inside ComfyUI with an opaque
  `LoadImage` error. It now raises "needs a `<REFERENCE_IMAGE>` — pin one with
  `/i2v-set-ref-image`" up front.

**Frontend**

- `state.js` — `t2vMode` and `currentText2VideoWorkflow`.
- `chat.js` — `runText2Video()`; a `text2video` branch in `runGeneration` (added to the
  `job` disjunction so still-image params and the auto-face-detail pass stay off a video
  run, plus the endpoint ternary and the video-settings body spread); the `sendMessage`
  and `#macro` intercepts; the header badge; session save/restore.
  - The non-obvious one: `originPrompt` derived from `state.imagePrompts[job.image]`.
    A t2v job has no `job.image`, so without a `text2video` branch the produced video
    would be stored with an **empty** prompt, breaking `/do-over`, `/i2v` and the
    metadata editor on it.
- `commands.js` — `/t2v`, `/t2v-workflow`, `/t2v-workflow-reset`; the `/workflows` table
  row; `/chat-summary` rows; `/help` entries; the `/settings` menu; `newChat` reset; the
  `/settings-save`/`-restore` snapshot.
- `autocomplete.js`, `templates/index.html` (`DEFAULT_TEXT2VIDEO_WORKFLOW`).

**Scope of the intercept:** the plain-prompt path in `sendMessage` and plain `#macro`
steps. Deliberately **not** `/sequence-run` (its loop runs server-side in
`/api/sequence-run`, where the client's mode flag has no reach) or `/multi-prompt`.

## Consequences

- A t2v template must have **no** `<INPUT_IMAGE>`; one that does fails the job with
  `Unfilled workflow placeholders: <INPUT_IMAGE>` — a clear error, not a silent misfire.
- Video models now need two templates each if you want both i2v and t2v.
- `/video-settings` drives both modes; the still-image `/image-settings` resolution and
  steps do not apply to t2v, exactly as for i2v.
- The mode persists with the chat session, but a session saved before `/t2v` existed
  restores with the mode **off** rather than inheriting whatever the previous chat had.

## Known unknown at time of writing

The derived MiniMax H3 template drops `first_frame` from `MiniMaxH3ImageToVideo`. If that
input turns out to be *required* rather than optional, ComfyUI will reject the graph and
the template must be rebuilt around the node pack's dedicated text-to-video class. This
must be test-rendered in the ComfyUI editor before use — which is precisely the check the
separate-dir approach makes possible.
