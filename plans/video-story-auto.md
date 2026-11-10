# Plan: `/video-story-auto` — chain ⏭ automatically through a video story

## Context
A `/video-story` currently needs one ⏭ click per clip: cut the last frame, give it the
next row, start the i2v, wait, then click ⏭ again on the new video. `/video-story-auto`
is a toggle (default **off**). While it's on, every video that finishes with a story row
still left after the cursor gets its ⏭ pressed automatically. One click (or one 🎬 on the
opening still) then runs the rest of the story.

## Behaviour
- `/video-story-auto` **toggles** the setting, like `/t2v`, and says which state it's
  now in. Turning it on starts nothing by itself. The message says: "press ⏭ on a video
  (or 🎬 the opening still) to start."
- **Trigger**: any single-image i2v run that completes (🎬 overlay, editor Start, review
  grid 🎬, and ⏭ itself, since they all go through `runImage2Video`). If it succeeds, the
  toggle is on, and `videoStoryNext(state.videoStory)` has a row, then
  `runStoryNext(newVideoUrl, …)` runs on the new video.
- **Stops** when: the toggle is off, there are no rows left, the generation failed or was
  cancelled (the still keeps its shot meta, so 🎬 retries it and picks the chain back up),
  the story was reset or replaced, or the app is busy with something else (`runStoryNext`
  already shows "Busy").
- Not triggered by ↺ video regenerate (`runVideoDoOver`), `/i2v <N>` batches or t2v.
  None of these go through `runImage2Video`.

## Changes (all client-side)

### `static/js/chat.js`
1. `runGeneration`: add an optional `opts.onResult(urls)`, called in the normal
   (non-slider, non-super-tile) `done` path just before `resolve(true)`, with
   `msg.images`. It's additive, so existing callers aren't affected.
2. `runImage2Video(prompt, image)`: pass `onResult` to capture the produced video URL.
   After `.finally` releases `sendBtn`, if `ok` and `shouldAutoStoryNext(...)` returns
   true, schedule `runStoryNext(videoUrl, null)` with `setTimeout(…, 0)`. This makes it a
   fresh call, not recursion, so each run's promise settles by itself and the ⏭ that
   started it re-enables.
3. `runStoryNext(videoUrl, btn)`: make `btn` optional (`if (btn) btn.disabled = …`). When
   it fires automatically, find the new video's ⏭ with
   `document.querySelector('.img-wrap video[src="…"]')` → `.img-story-next` and disable
   it, so a manual click can't double-fire.
4. Persistence, following `autoVideoSequence`: save `videoStoryAuto` in `saveSession`
   settings. `restoreSession` restores it **default-off**
   (`state.videoStoryAuto = !!s.videoStoryAuto`), so a mode that launches renders by
   itself can't carry over from another chat.

### `static/js/utils.js` (pure, unit-tested)
- `shouldAutoStoryNext({ auto, story, ok, url })` → `!!auto && ok && !!url &&
  isVideoUrl(url) && !!videoStoryNext(story)`. Put it next to `videoStoryNext`.

### `static/js/state.js`
- `videoStoryAuto: false, // /video-story-auto: press ⏭ automatically while rows remain`

### `static/js/commands.js`
- `/video-story-auto` handler next to `/video-story-reset`: flip the flag, `addMessage`
  on/off text, call `deps.updateHeaderStatus()` and a session save (dispatch
  `video-story-changed`, which already triggers `scheduleRecordSave`).
- `newChat`: `state.videoStoryAuto = false`.
- `/settings-save` / `/settings-restore`: add the key, with a guarded restore
  (`if (s.videoStoryAuto !== undefined)`), the same as `autoVideoSequence`.
- `/settings` summary: show an "Auto video-story: on" row when it's on.
- `/help`: add an entry under the `/video-story*` group.

### Docs
- Commit `plans/video-story-auto.md` before implementing (repo convention). Afterwards,
  rewrite it as `ADR/video-story-auto.md` and add a bullet to the "Continuous video
  stories" section of `CLAUDE.md`.

## Verification
- `node --check` on chat.js, commands.js, utils.js, state.js (curly-quote pitfall).
- `npm run test:js`: new `shouldAutoStoryNext` cases in `tests/js/utils.test.js` (off,
  failed run, non-video URL, no rows left, story null, rows left → true).
- `./scripts/test-all` for regressions.
- Manual: `/iterations 3`, `/video-story …`, 📋 row 1 onto an opening still, run
  `/video-story-auto`, then 🎬. Expect clips 2 and 3 to chain without clicks and stop
  after the last row. Cancel mid-chain and check that it stops. Toggle off mid-run and
  check that the current clip finishes and no next one starts.
