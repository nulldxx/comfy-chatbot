# /video-sequence-auto — image2video every /video-sequence shot automatically

## Decision

`/video-sequence-auto` sets `state.autoVideoSequence`; while it is on, each
`/video-sequence` shot is turned into a video as soon as its still is ready, after the
auto face-detail pass (which takes precedence). `/video-sequence-auto-reset` turns it off.

The work happens **inside the server-side sequence run** (`run_sequence_run`), not in the
browser. Each shot is now up to three ComfyUI stages on the job thread:

1. **generate** the still (unchanged);
2. **face-detail** it in place, when the request carries `autoFaceDetail`;
3. **image2video** the result, when this is a `/video-sequence` and the request carries
   `autoVideo`.

## Why server-side

A sequence run already lives on the server so it survives the tab closing and records
itself into the chat session; during the run the client is barred from writing that
session (`state.liveRunSession`). Queuing i2v jobs from the client's `image` handler would
have died with the tab and, worse, produced videos the session never recorded.

A consequence: **`/face-detail-auto` now applies to `/sequence` and `/video-sequence`
shots too.** It never did before — its pass lived in `runGeneration`, which sequence-run
images bypass — and "face-detail first" is meaningless without it. The client sends
`autoFaceDetail` whenever the flag is on, for either kind of run.

## How it was built

- **`prompt_builders.py`** (new, `COPY`'d in the Dockerfile) ports the pure builders the
  client uses — `apply_replacements`, `derive_face_detail_prompt`, `normalize_video_meta`,
  `build_video_prompt` — since Grok writes the shots after the request is made. I2VA only:
  every shot starts from its own still, so the pinned 🎞️ end frame is never used.
  `tests/test_prompt_builders.py` mirrors the JS cases; a one-off node/Python comparison
  over tricky prompts matched exactly.
- **Wire format** — `/api/sequence-run` accepts
  `autoFaceDetail: {workflow, prompt, replacements, denoise}` and
  `autoVideo: {workflow, duration, frames, fps, video_width, video_height, steps,
  video_opts, references, audio, overridePrompt, replacements}`.
  `app._parse_sequence_auto` validates both with the existing parsers
  (`resolve_workflow`, `_parse_denoise`, `_parse_video_settings`, `_parse_steps`,
  `_parse_video_opts`, `_resolve_references`), so a bad setting is a 400 before Grok is
  called. `autoVideo` is ignored unless `video` is true.
- **Stage loop** — the shot's pause-on-failure / ⟳ retry loop became `run_stage`, used by
  all three stages, so a failed face-detail or video retries **only that stage**. Stage
  failures carry `stage` on `shot_failed` and in `failed`, and are prefixed in the error.
- **Face-detail** mirrors the client's silent pass: prompt is `lastFaceDetailPrompt` or
  derived from the shot prompt, then face-detail replacements; skipped with no LoRA tag;
  runs with `preserve_mtime_from` the still, whose file (and seed entry) is then deleted.
  A cancel during it still records the undetailed still.
- **Image2video** builds the prompt the 🎬 button would (`startImage2VideoFor`): the
  override prompt verbatim, else `build_video_prompt(still prompt + i2v replacements,
  meta, audio)`. It emits a `shot` event with `stage: "image2video"` (the client opens a
  bubble with an `Image2video:` line) and an `image` event with the video URL. Like a
  client-side i2v, the video is stored against the still's prompt and meta;
  `append_session_image(message_prompt=…)` keeps the video prompt as the persisted user
  line.
- **Client** — `autoVideoSequence` is saved with the session (restored default-off, like
  `t2vMode`), in the `/settings-save` stack (guarded), reset by `newChat`, and shown in
  `/settings`. `runSequenceRunJob` sends the two blocks from current state (face workflow +
  denoise, i2v workflow, `/video-settings`, optimisations, steps, `referencesForRun(null)`,
  override prompt, replacements). The run's completion line counts videos separately.

## Limitations

- Settings are **snapshotted at run start**: changing `/video-settings` or `/references`
  mid-run doesn't affect the remaining shots.
- The shots' stills and videos interleave in ComfyUI's queue one shot at a time, so a
  15-shot run takes roughly 15 × (still + detail + video).
- A reference file deleted mid-run fails that shot's video stage (pauses for ⟳).
- A reattaching tab that joins mid-video falls back to a standalone bubble showing the
  still's prompt rather than the `Image2video:` line.
