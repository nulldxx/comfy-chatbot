# Plan: `/video-story <prompt>` — a continuous chain of video clips

## Context

`/video-sequence` makes N separate shots, each from its own still. There's no help yet for a
**continuous** story, where each clip starts from the previous clip's last frame (✂ cut last
frame) and its prompt picks up where that clip ended. `/video-story` asks Grok for N
follow-on video prompts, sized to the clip length set in `/video-settings`, and shows them
in an editable table. A row's 📋 button makes that row the source for the metadata editor's
Clone button. While a story is active, a ⏭ button on each video does the whole step in one
click: cut the last frame, attach the next row's metadata to it, and start the video.

Decisions made with the user:
- **Video rows only.** The user supplies the opening image. Grok writes no still prompt.
- **One story cursor.** A row's 📋 moves the cursor to that row. ⏭ uses cursor + 1 and then
  moves the cursor there. The table highlights the current row.

Per CLAUDE.md: first commit this plan to `plans/video-story.md`, and when the work is done
rewrite it as `ADR/video-story.md`.

## Server

**`grok.py`**
- Add `generate_video_story(master_prompt, count, clip_seconds, cancel_event=None, session=None)`.
  It returns `[{description, soundscape, music: "N/A"}]`.
- The prompt reuses the description and soundscape rules from
  `generate_video_prompt_sequence`: no appearance or setting, `(S1)` speaker IDs,
  `<d>[English] …</d>`, a single shot, no music. It adds the story rules:
  - Clip 1 starts from an opening image that matches the master prompt.
  - Clip *n+1* starts exactly where clip *n* ends, because its first frame is clip *n*'s
    last frame.
  - Each clip carries one beat of action sized to **about `clip_seconds` seconds**.
  - The clips build into a story with a beginning, a middle and an end. No clip resets
    the scene or repeats an earlier beat.
- Factor the model-fallback loop, which is currently duplicated, into
  `_ask_with_fallback(messages, max_tokens, parse, label, cancel_event, session)`. Use it in
  all three generators.
- Add `_parse_video_story(content)`. It is like `_parse_video_prompts` but needs no
  `prompt`: it keeps items that have a non-empty description and forces music to `N/A`.

**`app.py`**: add `@login_required POST /api/video-story` with body `{prompt, count, clip_seconds}`.
- Reuse `_parse_sequence_request` for the master prompt, the profanity check and the
  count clamp. A `ValueError` from it returns 400.
- `clip_seconds` must be a float from 0.5 to 60, with a default of 5. Anything else
  returns 400.
- Run Grok with `start_background_job(fn, kind="task", summary="Video story: …")` from
  `generation_service.py`. This avoids the gunicorn timeout when the call falls back to the
  second model. `fn` returns `{"items": [...]}`, which `done` carries to the client.
- Don't use `@requires_output_storage`, because nothing reads `IMAGES_DIR`.

## Client

**State** (`state.js`): add `videoStory: null`, which becomes
`{ master, clipSeconds, items: [{description, soundscape, music}], cursor: -1 }` once a story
is generated.
- It is chat content, like `lastSequence`. Save it in `saveSession` as a top-level key next
  to `lastSequence`, restore it in `restoreSession`, and reset it in the `newChat` block
  (`commands.js` ~1382).
- Leave it out of the `/settings-save` stack.

**Pure helpers** (`utils.js`, unit-tested in `tests/js/utils.test.js`)
- `videoStoryCount(iterations)` gives `iterations`. When iterations is 1 it gives a default
  of 5, mirroring how `/sequence` treats 1 as unset.
- `videoClipSeconds(vs)` uses the same `frames / fps` fallback that `videoPromptOpts`
  already uses at `utils.js:248`. Have `videoPromptOpts` call it, so both use one formula.
- `videoStoryNext(story)` gives `{index, item}` for `cursor + 1`, or `null` when there is no
  story or no row left.

**New module `static/js/story.js`**: `renderVideoStoryTable(bubble)`
- Columns: #, Description (textarea), Soundscape (textarea), 📋.
- Typing in a textarea writes back to `state.videoStory.items[i]` and calls
  `scheduleRecordSave`.
- 📋 sets `state.lastVideoMeta = { prompt: '', ...item }`. That is exactly what the
  editor's Clone button reads (`chat.js:565`), so Clone needs no changes. 📋 also sets
  `cursor = i`, highlights the row, posts a short "Shot i copied — use 📋 Clone in the
  metadata editor" note, and dispatches `video-story-changed`.
- The header shows the master prompt and the clip length used.

**Commands** (`commands.js`, plus `autocomplete.js` and the `/help` table)
- `/video-story <prompt>`
  - Run `expandAliases`. If the prompt is empty, show an error.
  - POST `count` from `videoStoryCount(state.iterations)` and `clip_seconds` from
    `videoClipSeconds(state.currentVideoSettings)`.
  - Stream `/api/progress/<id>` with the same EventSource pattern as `/fscheck`
    (`commands.js:3200`).
  - On `done`, store `state.videoStory` with `cursor: -1`, render the table, dispatch
    `video-story-changed` and save.
- `/video-story-review` re-renders the table for the current story.
- `/video-story-reset` sets `state.videoStory = null`, which hides the ⏭ buttons.

**⏭ button** (`chat.js` `appendChatImage`, video branch next to `.img-cut` at ~1058)
- Add a `.img-story-next` button. In `chat.css`, give it the same style as `.img-cut`,
  placed so its right edge is 5px left of ✂: the `right` offset of `.img-cut` plus its
  width plus 5px.
- `refreshStoryNextButtons()` toggles `display` and the tooltip ("Next story shot 3/6:
  <excerpt>") on every `.img-story-next`, following the `refreshLastFrameButtons` pattern.
  It runs from a `video-story-changed` listener and after each render.
- Click handler:
  1. If `sendBtn.disabled`, show the existing "Busy" message and stop.
  2. Get `next = videoStoryNext(state.videoStory)`.
  3. Change `extractLastFrame(url)` so it resolves to the new still's URL, or `null` on
     failure. The existing ✂ caller ignores the value, so it is unaffected.
  4. Set `state.imageVideoMeta[newUrl] = { ...next.item }`. This overwrites the metadata
     that `extractLastFrame` copied from the video.
  5. Set `cursor = next.index`, dispatch `video-story-changed`, and call
     `startImage2VideoFor(newUrl)`.
- If the video fails, the cut still keeps its story metadata, so 🎬 or Start retries
  without skipping a row.
- Existing behaviour stays as it is: an `/image2video-set-prompt` override or a 🎞️ end
  frame still apply.

After every JS edit, run `node --check` (see the curly-quote pitfall).

## Docs
- Add a `/video-story` section to `CLAUDE.md` next to the `/video-sequence` ones.
- Add `ADR/video-story.md` (the rewritten plan).
- Add `/help` and autocomplete entries.

## Verification
- `python -m pytest tests/test_grok.py tests/test_app_routes.py -v`, adding tests for:
  - `_parse_video_story`: forces music to N/A and drops items with no description.
  - The prompt includes the clip seconds and the count.
  - `/api/video-story`: returns 400 for a missing prompt or a `clip_seconds` out of range;
    with `generate_video_story` mocked, `done` carries `items`.
- `npm run test:js` for the three helpers, and `./scripts/test-all`.
- Manual test with `docker-compose up --build -d`:
  1. Run `/iterations 4`, then `/video-story two friends get lost hiking` to get a 4-row
     table. Edit a cell, reload, and run `/video-story-review`; the edit is still there.
  2. Press 📋 on row 1. Open ✏ on an existing image, press Clone, and check the fields are
     filled. Press Start.
  3. On the resulting video, check ⏭ sits 5px left of ✂. Click it: the last frame appears
     with row 2's metadata, a video starts, and row 2 is highlighted.
  4. After the last row, check ⏭ is hidden. Run `/video-story-reset` and check every ⏭
     disappears.
