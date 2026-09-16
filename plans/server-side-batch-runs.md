# Server-side batch runs (`/api/batch-run`)

## Context

`docs/needs-server-side-implementation.md` lists six commands whose *loop* still lives in
the browser: `/face-detail-auto` on plain prompts, `/i2v <N>`, `/iterations`,
`/multi-prompt`, `/face-detail <N>` / `/face-detail-session`, and `/t2i-workflow-iterate`.
Each ComfyUI job already runs on a server thread, but the browser decides what runs
next. Close the tab and the chain stops after the current job. Any job that does finish
lands in the gallery but not the chat, because only `run_sequence_run` writes the session
from the server.

**Outcome:** one general server-side **batch job**. Each of those commands builds a
list of steps and posts it once. The job runs the steps in order on one thread, records
every result into the chat session and streams progress. The client renders the stream
with the existing sequence-run renderer. Closing the tab no longer stops the run, and
results still reach the chat. Macros stay client-side, as the doc recommends.

(The doc's line numbers have drifted. Current locations: `/multi-prompt` `commands.js:1802`,
`/i2v` ~`:1976`, `/t2i-workflow-iterate` `:2392`, `/face-detail-session` `:2495`,
`/face-detail` `:2517`, the plain-prompt iterations loop `chat.js:~1585`, and the client
auto face-detail pass in `runGeneration` `chat.js:~2290`.)

## Step 0 — process (per CLAUDE.md)

Save this plan as `plans/server-side-batch-runs.md` and commit it before starting. When
the work is done, rewrite it as `ADR/server-side-batch-runs.md`. Also update the survey
doc to move these commands into "Already server-side", and add a CLAUDE.md section.

## Design decisions (settling the doc's two open questions)

1. **Prompt derivation: explicit steps carry fully built prompts, built by the client.**
   For `/i2v N`, `/face-detail N` and `/face-detail-session`, every input already
   exists when the request is made (`imagePrompts`, `imageVideoMeta`, replacements,
   override prompt, 🎞️ end frame). So the client builds each prompt with the code it
   uses today (`buildVideoPrompt(…, videoPromptOpts(state, img))`,
   `deriveFaceDetailPrompt`, `applyReplacements`) and sends it as the step's prompt.
   This keeps FL2VA end-frame prompts exact; the server port in `prompt_builders.py`
   is I2VA-only. Images with no derivable prompt are reported and skipped on the client
   before the post, just as the loops skip them today.
   Only the **auto face-detail** stage derives its prompt server-side, because it runs
   on an image that doesn't exist yet. It reuses the existing `face_auto` code path and
   `prompt_builders.py`, so there are no new ports and no parity changes.
2. **Settings snapshotting:** the request has one validated settings block per kind
   (`t2i`, `video`, `face`), and a step may override only `workflow`. Everything is
   validated in `app.py` with the ordinary parsers before the job starts, so a bad
   request returns 400.

Other decisions (stated assumptions):
- **Failure pauses the run, like a sequence run.** A failed stage waits for ⟳ retry
  or ✕ cancel. Today's client loops stop on failure (`if (!ok) break`) instead. This is
  what the doc means by "reuse per-stage retry".
- **A single plain prompt keeps the existing `runGeneration` path.** It is not a loop.
  A plain prompt goes through a batch only when `state.iterations > 1`, or when
  `state.autoFaceDetail` is on and `/t2v` is off (the generate-then-detail chain).
  The five commands **always** use a batch, even for N=1, so each command has one code
  path.
- **`/getseed` is kept:** a pinned `state.reuseSeed` travels as a top-level `seed` and
  applies to the first t2i/t2v/i2v step. It is cleared once the request is accepted.
  Every t2i/t2v/i2v step passes `track_seed=True`.
- **Macros are unchanged.** Plain macro steps still call `runGeneration` (so the
  client-side auto face-detail code stays, for macros only). A macro step such as
  `/i2v 3` still works because the command returns a promise that resolves only once
  the batch finishes and its images are in `state.sessionImages`.

## Server

### Wire format — `POST /api/batch-run` (`@login_required`, `output_storage_error()` gate)

```jsonc
{
  "recordingName": "temp-…",
  "settings": {
    "server", "server_os",
    "t2i":   { "workflow", "width", "height", "steps", "extraPrompt" },     // when any t2i step
    "video": { "i2vWorkflow", "t2vWorkflow", "duration", "frames", "fps",
               "video_width", "video_height", "steps", "video_opts", "references" },
    "face":  { "workflow", "denoise" }
  },
  "autoFaceDetail": { "workflow", "prompt", "replacements", "denoise" },    // optional, t2i steps only
  "seed": "123…",                                                            // optional, first primary step
  "steps": [
    { "kind": "t2i",         "prompt", "workflow"?, "label"? },
    { "kind": "t2v",         "prompt" },
    { "kind": "i2v",         "prompt", "image", "last_frame"?, "sourcePrompt"?, "videoMeta"? },
    { "kind": "face-detail", "prompt", "image", "sourcePrompt"?, "videoMeta"? }
  ]
}
```

`app.py` adds `_parse_batch_run(data)`, which reuses the existing validators:
- `_parse_gen_settings`, which needs a small refactor so it can read the `t2i`
  block. Keep its current signature working for `/api/sequence-run`.
- `_parse_video_settings`, `_parse_steps`, `_parse_video_opts`, `_resolve_references`
  (these run once, and only if a video step exists), `_parse_denoise`, `_parse_seed`.
- `resolve_workflow` per distinct step workflow, checked against that kind's list
  (`list_workflows` / `list_image2video_workflows` / `list_text2video_workflows` /
  `list_facedetailer_workflows`).
- `resolve_input_image` for every `image` / `last_frame`.
- `parse_loras_from_prompt`. As in the endpoints, i2v/t2v discard LoRAs, and a t2i or
  face-detail step that is empty after tag removal is a 400.
- The `autoFaceDetail` block is parsed by extracting the `face` half of
  `_parse_sequence_auto` into `_parse_auto_face(block)`, shared by both routes.
- Limits: at least 1 step and at most `BATCH_MAX_STEPS` (new in `config.py`, e.g. 500,
  so `/face-detail-session` on a big chat still fits). Unknown `kind` → 400.

### Job — `generation_service.py`

**Refactor first, with no behaviour change.** Lift the per-shot pipeline out of
`run_sequence_run` into a reusable runner. Today it is the `run_stage` and `record`
closures plus the generate → face-auto → video-auto body (`generation_service.py:951–1160`).
Proposed shape: a small `_StepRunner` class, or a factory returning closures, built
from `(job_id, channel, cancel_event, retry_event, gen_settings, auto, failed, all_urls)`
and exposing:
- `run_stage(i, total, fail_prompt, progress_msg, retry_msg, fn, stage=None)`, unchanged
- `record(url, prompt, video_meta, i, total, message_prompt=None, stage=None)`, unchanged
- `run_still(i, total, item_prompt, video_meta, gen_fn)`: the generate → auto
  face-detail → (auto video) chain, which `run_sequence_run` calls per shot.

`run_sequence_run` keeps its Grok phase and `prompts` event and then loops using the
runner. Its existing tests in `tests/test_generation_service.py` must pass
**unmodified**; that is the check on the refactor.

**New** `run_batch_run(job_id, steps, settings, auto, seed)` / `start_batch_run_job(...)`:
- The job record has the same shape as a sequence run (`retry` event, `recording_name`,
  `server`, `prompt_id`), with `kind: "batch-run"` and summary
  `_build_summary(workflow, first prompt, "batch-run")` plus the step count. There is
  no `session` (no Grok call); `api_cancel` already handles a missing session.
- For each step `i`: emit `shot` with `{index, total, prompt, stage: kind, label}`,
  then dispatch:
  - `t2i` → `runner.run_still(...)` wrapping `_run_generation_core` with the `t2i`
    settings, `extraPrompt` appended to the prompt sent for generation only (as in
    sequence runs), `track_seed=True` and the step's workflow override.
    Auto face-detail comes free from the runner.
  - `t2v` → `run_stage` + `_run_generation_core(workflow_dir=COMFY_TEXT2VIDEO_DIR, …video kwargs, refs)`,
    recorded with `prompt` as the origin prompt.
  - `i2v` → the same with `COMFY_IMAGE2VIDEO_DIR`, `input_image`, `input_last_frame`,
    recorded as `record(v, sourcePrompt, videoMeta, …, message_prompt=prompt)`. This
    matches what `runImage2Video` stores and what auto-video already does.
  - `face-detail` → `COMFY_FACEDETAILER_DIR`, `input_image`, `denoise`,
    `preserve_mtime_from`. The result is a **new** image and the source is kept, since
    `/face-detail N` currently shows the result in a new bubble rather than in-place.
    Recorded against `sourcePrompt` / `videoMeta`.
  - The references lists are copied per call (`list(refs[...])`), as auto-video does.
- Terminal handling: `done` with `{images, failed, batch: true}`, plus the same
  cancelled/error branches as `run_sequence_run` without the Grok case.

### Plumbing

- `app.api_jobs`: add `"batch-run"` to the kind filter and update the docstring.
- `api_retry_shot` needs no change; it keys off the `retry` event.
- `_idle_busy` / `_logoff_refusal` need no change; they check any non-terminal job.
- `generation_service.py:37`: add the kind to the record-shape comment.

## Client

### A shared launcher — `chat.js`

Add `runBatchJob(steps, { headerText })`, a sibling of `runSequenceRunJob` sharing its
skeleton:
- Builds the `settings` blocks from state, taking the same fields `runGeneration` and
  `runSequenceRunJob` send today: `currentResolution`, `currentGenerationSteps`,
  `extraPrompt`, `currentVideoSettings`, `videoOptsPayload`, `currentVideoSteps`,
  `referencesForRun(null)`, `currentDenoise.face`, and the per-kind workflows with their
  `DEFAULT_*` fallbacks.
- Adds `autoFaceDetail` when `state.autoFaceDetail`, in exactly the `runSequenceRunJob`
  form. Factor that block into a helper `autoFaceDetailPayload()` used by both.
- Adds `seed` from `state.reuseSeed`, clearing it after acceptance.
- Sets `state.liveRunSession`, then `attachSequenceRunStream(jobId, …)`, and returns a
  promise that resolves `true` / `false` on done or fail. It disables `sendBtn` for the
  run and re-enables it at the end, unless the caller already owns the button (the
  macro loop): take `{ ownsSendBtn }`.
- **i2v references:** `referencesForRun(image)` suppresses image slot 1 when it equals
  the triggered image. The batch sends `referencesForRun(null)` once, and
  `run_batch_run` applies the same rule per i2v step: drop slot 1 when it resolves to
  the step's image, so the `<INPUT_IMAGE>` fallback applies.

### `attachSequenceRunStream` generalisation

- The `shot` label comes from `msg.stage`: `image2video`/`i2v` → `Image2video: `,
  `face-detail` → `Face detail: `, `t2v`/`t2i` → none. Append `msg.label` (e.g.
  ` — wf (2/5)` for iterate, ` (3/8)` for iterations) to the status text.
- `image` event: `sessionImages`/`imagePrompts`/`imageVideoMeta` updates stay as they
  are. For i2v steps, also set `state.lastVideoMeta` from the source, as
  `runImage2Video` does, so the metadata editor's Clone button still works.
- `done`: when `msg.batch`, show `Batch complete — N image(s), M video(s)` instead of
  "Sequence complete".
- Rename the startup/reattach helpers' kind check to a shared `isLiveRunKind(kind)`
  (`sequence-run` | `batch-run`) in `resumeRunningSequenceRunOnStartup` and
  `reattachLiveSequenceRun`, so a batch survives a reload or `/session-load` the same
  way.

### Callers

| Caller | Change |
|---|---|
| `sendMessage` plain prompt (`chat.js`) | If `iterations > 1` or (`autoFaceDetail && !t2vMode`): add one user line, then `runBatchJob` with N `t2i`/`t2v` steps, `label: ' (i/N)'`. Otherwise the existing single `runGeneration`/`runText2Video` call. |
| `/multi-prompt` | One `t2i` step per alias-expanded line. The user lines come from `shot` events, so remove the per-line `addMessage`. |
| `/t2i-workflow-iterate` | One `t2i` step per ticked workflow with a `workflow` override and label. |
| `/i2v <N>` | Build prompts as today and stop at the first image with no prompt, as today (report it and post the steps before it). Each step is `{kind:'i2v', prompt, image, last_frame (when lastFrameUrl ≠ image), sourcePrompt, videoMeta}`. |
| `/face-detail <N>`, `/face-detail-session` | Build prompts as today, skip underivable ones with the existing warning, and post the rest as `face-detail` steps. |

Every one returns the `runBatchJob` promise so `await handleSlashCommand` in macros
still waits for it. Remove the `runGeneration` auto-face branch only if no non-macro
caller still reaches it; otherwise leave it with a comment saying it is macro-only.

Update `/help` entries and `/t2v` notes where they say `/multi-prompt` is unaffected by
t2v: that stays true (multi-prompt stays t2i), but check the wording.

## Tests

**Python**
- `tests/test_generation_service.py`: existing `run_sequence_run` tests pass
  unchanged. New `run_batch_run` tests, mocking `_run_generation_core`:
  - Steps run in order with the right `workflow_dir`/kwargs per kind.
  - A t2i step gets auto face-detail and the still is replaced (`_discard_gallery_file`).
  - i2v records `sourcePrompt` with `message_prompt=prompt`.
  - A face-detail step keeps the source.
  - A failed stage pauses, and a retry re-runs only that step.
  - Cancel mid-batch marks the job cancelled with partial assets.
  - The seed applies only to the first primary step.
  - Every result reaches `append_image_to_recording`.
  - Image slot 1 is suppressed when it equals the step image.
- `tests/test_app_routes.py` (or a new `tests/test_batch_run.py`) for `/api/batch-run`:
  - 400s: no steps, too many steps, unknown kind, bad workflow, bad image URL, bad
    video settings, empty-after-LoRA prompt, missing recordingName.
  - 503 when storage isn't ready.
  - Only the needed blocks are validated.
  - Happy path returns a `job_id` and calls `start_batch_run_job` with parsed steps.
- `tests/test_jobs_api.py`: `batch-run` jobs are listed with `recording_name`.

**JS** (`tests/js/`): unit-test any pure helpers extracted, such as a
`buildBatchSteps`-style builder for `/i2v`/`/face-detail` prompts and the stage → label
mapping, if they are pulled into `utils.js`.

## Verification

1. `./scripts/test-all` and `npm run test:js`. Run `node --check` on every edited JS
   file (curly-quote pitfall).
2. `docker-compose up --build -d` against a real ComfyUI server:
   - `/iterations 3` plus a prompt: three shells stream in. Close the tab after shot 1
     and reopen: the page reattaches, shots 2–3 finish, and `/session-load` shows all
     three.
   - `/face-detail-auto`, then a single prompt with a `<lora:…>` tag: generate →
     face-detail in one batch, with the still replaced.
   - `/i2v 2` with a 🎞️ end frame pinned: FL2VA prompt text in the user line, two
     videos recorded.
   - `/face-detail 2`, `/multi-prompt` (2 lines), `/t2i-workflow-iterate` with 2
     workflows ticked.
   - Stop the ComfyUI server mid-batch: the shot pauses on ⟳. Start it via
     `/server-status`, press ⟳ and the run continues.
   - A macro containing `/face-detail 1` followed by `/upscale`: the macro waits for
     the batch, and `/upscale` targets the face-detailed image.
   - `/logoff` during a batch returns 409.
3. Release via the `push-to-portainer` skill, confirming with the user before the
   Portainer redeploy.
