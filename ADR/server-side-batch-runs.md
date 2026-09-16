# Server-side batch runs (`/api/batch-run`)

## Context

`docs/needs-server-side-implementation.md` surveyed the multi-step commands whose *loop*
lived in the browser: `/iterations`, `/multi-prompt`, `/t2i-workflow-iterate`,
`/i2v <N>`, `/face-detail <N>`, `/face-detail-session`, and a plain prompt under
`/face-detail-auto`. Each ComfyUI job already ran on a server thread, but the browser
decided what ran next. Close the tab and the chain stopped after the current job. Any
job that did finish landed in the gallery but never in the chat, because only sequence
runs wrote the session from the server.

## Decision

One general server-side **batch job** rather than an endpoint per command. Each command
builds a list of steps and posts it once to `POST /api/batch-run`. The job runs the steps
in order on one thread, records every result into the recording session and streams
progress. The browser renders it with the sequence-run renderer.

Macros stay client-side. A macro step can be any slash command, and those read browser
state.

### Shared runner

`run_sequence_run`'s per-shot machinery was lifted into `_StepRunner`
(`generation_service.py`), and both run kinds are built from it. It provides:
- `run_stage`: the pause-on-failure / ⟳ retry loop.
- `record`: persist to the session and emit `image`.
- `run_still`: generate → auto face-detail → auto image2video.
- `finish`: the terminal bookkeeping.
- `core`: `_run_generation_core` bound to the run's server and events.

`_start_run_job` creates the job record both kinds share. The refactor changed no
behaviour: the existing sequence-run tests pass unmodified.

`run_batch_run` dispatches by step kind:

| kind | runs | recorded as |
|---|---|---|
| `t2i` | generation dir, t2i settings, `extraPrompt` appended for generation only; auto face-detail via `run_still` | the typed prompt |
| `t2v` | text2video dir, video settings, references | the prompt, stage `text2video` |
| `i2v` | image2video dir, source image + optional end frame, references with image slot 1 dropped when it *is* the source | source prompt/meta, user line = i2v prompt, stage `image2video` |
| `face-detail` | facedetailer dir, denoise, `preserve_mtime_from`; the source is **kept** | source prompt/meta, user line = face prompt, stage `face-detail` |

A pinned `/getseed` seed goes to the first t2i/t2v/i2v step only. Every such step
passes `track_seed=True`.

### Where prompts are built

Explicit steps carry **fully built prompts**, built by the client when the command runs.
Every input (`imagePrompts`, `imageVideoMeta`, replacements, override prompt, 🎞️ end
frame) already exists at that point. `buildI2vSteps` / `buildFaceDetailSteps`
(`utils.js`) call the same helpers the single-image buttons use. This keeps FL2VA
end-frame prompts exact, which `prompt_builders.py` (I2VA-only) could not have done.
Only the auto face-detail pass on a t2i step derives its prompt server-side, since its
image doesn't exist yet. It reuses the sequence run's existing code path.

### Settings snapshot and validation

The request carries one settings block per kind (`t2i`, `video`, `face`), and a step may
override only its `workflow`. `app._parse_batch_run` validates each block only when a
step needs it, using the ordinary parsers:
- `_parse_image_settings`, now shared with `_parse_gen_settings`.
- `_parse_video_settings`, `_parse_steps`, `_parse_video_opts`, `_resolve_references`,
  `_parse_denoise`, `_parse_seed`.
- `_parse_auto_face`, now shared with `_parse_sequence_auto`.

It also resolves every step's workflow against its kind's allowlist and every image via
`resolve_input_image`. So a bad request is a 400, or 404 for a missing image, before any
job starts. t2i workflows are not allowlisted, matching `/api/generate` and
`/api/sequence-run`. The step count is capped at `BATCH_MAX_STEPS` (500, `config.py`).

## Client

- `runBatchJob(steps)` (`chat.js`) posts the snapshot, suppresses the client's own
  session save (`state.liveRunSession`) and attaches `attachSequenceRunStream`. It
  resolves when the batch ends, so `await handleSlashCommand` in a macro still waits for
  `/i2v 3`.
- `attachSequenceRunStream` learned three things:
  - A shot's `stage` picks its user-line prefix (`runStagePrefix`).
  - A step's `label` (` (2/5)`, ` — wf (1/3)`) trails its status lines.
  - An `image2video` result updates `state.lastVideoMeta`, as `runImage2Video` does.
  `done` with `batch: true` reads "Batch complete".
- `isLiveRunKind` makes startup resume and `/session-load` reattach find `batch-run`
  jobs as well as `sequence-run` ones. `/api/jobs` lists the new kind.
- **Routing:** a typed prompt uses a batch when `iterations > 1`, or when
  `/face-detail-auto` is on outside `/t2v`. A single prompt otherwise keeps the old
  `runGeneration` path. The five commands always use a batch. Each shot writes its own
  user line, so the typed prompt is no longer echoed separately for a batch.
- `runGeneration`'s client-side auto face-detail pass is now reached only by macro
  steps.

## Consequences

- **A failure pauses instead of stopping.** The old loops broke on the first failed job.
  A batch pauses on ⟳ retry or ✕ cancel, like a sequence run, so one bad step can't
  silently drop the rest.
- `/face-detail <N>` and `/i2v <N>` **skip videos** with a note. Previously each one
  failed individually. An image with no derivable face prompt is skipped with one
  aggregated warning, not one per image.
- While a batch runs, the server is the sole writer of the session. That is the same
  trade-off sequence runs already make.
- A typed `/iterations N` now shows the prompt once per shot rather than once overall.
  That matches what the persisted session always contained.
- **Not covered:** macros (by design), the 🎬/face icon single-image buttons (not
  loops), `/upscale <N>` (not in the survey; still a client-side chain).
