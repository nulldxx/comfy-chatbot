# `/video-story-auto` — chain ⏭ automatically through a video story

## Context
A `/video-story` needed one ⏭ click per clip. `/video-story-auto` is a toggle (default
off) that presses ⏭ for you, so one click (or 🎬 on the opening still) runs the story.

## Decision
- **Toggle** (`state.videoStoryAuto`). Turning it on starts nothing by itself.
- **Trigger lives in `runImage2Video`** (`chat.js`), which every single-image i2v launch
  goes through: 🎬, the editor's Start, the review grid's 🎬, and ⏭ itself. When a run
  succeeds and `shouldAutoStoryNext` (`utils.js`, pure, unit-tested) returns true,
  `runStoryNext(newVideo, null)` is **scheduled** with `setTimeout`. Scheduling it rather
  than awaiting it means each clip's promise settles and its button re-enables, and the
  chain doesn't nest promises.
- **Getting the new URL**: `runGeneration` gained an optional `opts.onResult(urls)`,
  called on the ordinary done path. `runGeneration` still resolves to a boolean.
- **Automatic presses disable the video's own ⏭** (looked up by `src`), so a manual click
  can't run the same shot twice.
- **Stops** at the last row, on a failed or cancelled clip, when toggled off, when the
  story is reset or replaced, or when the app is busy. A failed clip's still keeps its
  shot meta, so 🎬 retries it and the chain picks up again.
- **Not triggered** by ↺ regenerate, `/i2v <N>` batches or t2v. They don't use
  `runImage2Video`.
- **Persistence**: saved with the session and restored **default-off** (it launches
  renders by itself). It's in the `/settings-save` stack (guarded restore), reset by
  `newChat`, and shown in `/settings` when on.

## Consequences
Client-side only: the chain dies if the tab closes, unlike `/video-sequence-auto`.
Accepted, because ⏭ itself (cutting a frame, editing meta) is client-side.
