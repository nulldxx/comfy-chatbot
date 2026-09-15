# /video-sequence-auto — image2video every shot of a /video-sequence automatically

## Context

`/video-sequence` asks Grok for a plan of shots, then generates a still per shot and stores
MiniMax H3 video metadata (`{description, soundscape, music}`) against each one. Turning
a still into a video is manual: the 🎬 button, per image.

The run is driven **server-side** (`/api/sequence-run` → `run_sequence_run`) so that it
survives the browser closing and records every image into the chat session itself.
While it runs, the client's own session auto-save is suppressed (`state.liveRunSession`)
so the server is the sole writer.

Auto face-detail (`/face-detail-auto`) is purely **client-side**: `runGeneration` queues
a face-detail pass after a plain t2i generation. It never touched sequence runs, because
their images arrive through `attachSequenceRunStream`, not `runGeneration`.

**Outcome:** `/video-sequence-auto` sets a flag; while it is on, every shot of a
`/video-sequence` is turned into a video as soon as its still is ready — after the
auto face-detail pass, which takes precedence. `/video-sequence-auto-reset` turns it off.

## Approach: extend the server-side loop

Doing it client-side (queue an i2v run on each `image` event) was rejected: the chain
would die with the tab, and the videos would never reach the session file because the
client is not allowed to write it during a live run.

So each shot of `run_sequence_run` becomes up to three ComfyUI stages on the job thread:

1. **generate** — unchanged.
2. **face-detail** — when the request carries `autoFaceDetail`. The detailed image
   replaces the still (original deleted, `preserve_mtime_from` the original), exactly as
   the client's silent in-place pass does. Skipped when no face prompt can be derived
   (no `<lora:…>` tag), as client-side. This also makes `/face-detail-auto` apply to plain
   `/sequence` runs — it is the same loop and the flag already reads "every new generation".
3. **image2video** — when `video` and the request carries `autoVideo`. Runs on the
   (face-detailed) still; the video is recorded to the session and streamed like an image.

Each stage uses the existing pause-on-failure / ⟳ retry loop, factored into one helper
so a failed face-detail or video retries **that stage**, not the whole shot.

### Prompts built server-side

The prompts are assembled client-side today, from state the server never sees. The
client therefore sends the settings, and the server ports the two pure builders:

- `prompt_builders.py` (new): `apply_replacements`, `derive_face_detail_prompt`,
  `normalize_video_meta`, `build_video_prompt` — line-for-line ports of `utils.js`,
  I2VA only (an auto run never has a 🎞️ end frame: every shot starts from its own still,
  and the pinned end frame belongs to some other image).

### Wire format (`/api/sequence-run`)

```jsonc
"autoFaceDetail": { "workflow", "prompt" /* lastFaceDetailPrompt */, "replacements", "denoise" },
"autoVideo": {
  "workflow", "duration", "frames", "fps", "video_width", "video_height",
  "steps", "video_opts", "references", "audio", "overridePrompt", "replacements"
}
```

Both validated up front in `app.py` with the existing `_parse_video_settings`,
`_parse_steps`, `_parse_video_opts`, `_resolve_references`, `_parse_denoise` and
`resolve_workflow`, so a bad setting is a 400 before Grok is called. `autoVideo` is
ignored when `video` is false.

### Streaming & persistence

- The video stage emits a `shot` event with `stage: "video"` (client opens a new bubble
  with an `Image2video: <prompt>` user line) and then an `image` event with the video URL,
  carrying the still's prompt and video meta — what `runImage2Video` stores for a video.
- `append_session_image` gains `message_prompt` so the persisted user line is the video
  prompt while `imagePrompts[video]` stays the still's prompt.
- A cancel during face-detail still records the undetailed still.

## Client

- `state.autoVideoSequence` (default off); session save/restore (restore defaults off, like
  `t2vMode`, so an expensive mode can't leak between chats), `/settings-save` stack,
  `newChat` reset, `/settings` summary row.
- `/video-sequence-auto`, `/video-sequence-auto-reset` commands, autocomplete, help.
- `runSequenceRunJob` sends `autoFaceDetail` / `autoVideo`.
- `attachSequenceRunStream` honours `stage` on `shot`; completion line counts videos.

## Tests

- `tests/test_prompt_builders.py` — parity cases from `tests/js/utils.test.js`.
- `run_sequence_run`: face-detail replaces the still; skipped without a LoRA; video runs
  after face-detail with the built prompt; override prompt; not run for `/sequence`;
  video-stage failure retries only that stage; cancel during face-detail keeps the still.
- `/api/sequence-run`: auto blocks parsed/validated/ignored.
- `append_session_image(message_prompt=…)`.
- Dockerfile: `COPY prompt_builders.py`.
