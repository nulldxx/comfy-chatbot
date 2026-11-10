# ADR: `/video-story` — a continuous story of chained video clips

## Context

`/video-sequence` makes N independent shots, each from its own still. There was no help
for a **continuous** story where each clip starts from the previous clip's last frame
(✂ *Cut last frame*) and its prompt carries on from where that clip ended.

## Decision

`/video-story <prompt>` asks Grok for `/iterations` follow-on video prompts (5 when
iterations is the default 1), each sized to the clip length `/video-settings` produces,
and shows them as an editable table. The user supplies the opening image; Grok writes
**no** still prompts.

### Server
- `grok.generate_video_story(master, count, clip_seconds)` → `[{description, soundscape,
  music: "N/A"}]`. The prompt reuses `/video-sequence`'s H3 description/soundscape rules
  (no appearance, `(S1)` speaker IDs, `<d>[English] …</d>`, single shot, no music) and
  adds the story rules: clip n+1 starts exactly where clip n ends, one beat per clip of
  about `clip_seconds`, a beginning/middle/end, no resets or repeated beats, stable
  speaker IDs across clips. Parsed by `_parse_video_story` (accepts `clips` or `prompts`,
  drops items without a description).
- The primary→fallback model loop, previously duplicated, is now
  `grok._ask_with_fallback`, used by all three generators.
- `POST /api/video-story {prompt, count, clip_seconds}` (`app.py`): validated by
  `_parse_sequence_request` (profanity filter, count clamp) plus `clip_seconds` ∈
  [0.5, 60]; runs on `start_background_job` (two Grok attempts can outlast the worker
  timeout) and the `done` SSE event carries `items`.

### Client
- `state.videoStory = { master, clipSeconds, items, cursor }`, where `cursor` is the row
  last used (-1 = none). Chat content like `lastSequence`: saved/restored with the
  session, reset by `newChat`, not in the `/settings-save` stack.
- **One story cursor.** `story.js` renders the table (editable description/soundscape
  textareas, 📋 per row). 📋 sets `state.lastVideoMeta` — exactly what the metadata
  editor's Clone button already reads, so Clone needed no change — and moves the cursor
  to that row.
- **⏭ on every video** (`chat.js`, beside ✂) while `videoStoryNext(state.videoStory)` is
  non-null: cut the last frame (`extractLastFrame` now resolves to the new URL), set that
  still's `imageVideoMeta` to the next row, advance the cursor, and
  `startImage2VideoFor()` it. If the video fails, the still keeps the row, so 🎬 / Start
  retries it without skipping. An `/image2video-set-prompt` override or a 🎞️ end frame
  still apply, as for any i2v launch.
- ✂ sits at the top-left corner of the video, so ⏭ goes 5px to its **right** (a
  `.img-topleft` flex holder with `gap: 5px`), not to its left.
- Every change dispatches `video-story-changed`; `chat.js` listens to save the session
  and refresh the ⏭ buttons, so `story.js` imports nothing from `chat.js`.
- Pure helpers in `utils.js` (unit-tested): `videoStoryCount`, `videoStoryNext`,
  `videoClipSeconds` (now also used by `videoPromptOpts`).
- `/video-story-review` re-shows the table (table bubbles aren't persisted with the
  chat); `/video-story-reset` clears the story and hides every ⏭.

### Row editing (added later)
- Click a row to make it the cursor; clicking the current row clears it (-1), so ⏭
  starts again from shot 1.
- ➕ *Insert row* / 🗑 *Delete row* under the table act on the cursor: insert puts a blank
  shot after it (top with no cursor) and selects it; delete removes it and moves the
  cursor to the previous row, so ⏭'s next shot is the one that followed. Helpers
  `videoStoryInsert`, `videoStoryDelete`, `videoStorySetCursor` in `utils.js`.
- `renderVideoStoryTable` stores its listener cleanup on the bubble so re-renders don't
  stack listeners, and rebuilds rows when another open copy changed the row count.

- Restoring a chat (reload, sidebar, `/session-load`) re-renders the table at the end
  of the chat when the session has a story; the bubble itself still isn't persisted.

### Character summary and voice details (added later)
Voice details from the master prompt — a British accent, say — were vanishing. The
"never describe people" rule read to Grok as "drop character traits", the only voice
example was "in a deep voice", and nothing said a detail must be repeated, though each
clip's text reaches H3 on its own. And because clips chain off cut last frames, the
characters drifted over a story. So the `generate_video_story` prompt now says:
- **Each description opens with one sentence summarising how the characters look**
  (build, age range, hair, clothing), worded the same in every clip, then 1–3 sentences
  of action. The scene — room, location, weather, lighting, style, lens — is still never
  described: H3 sees it in the starting frame.
- **Every voice detail** (accent, pitch, pace, tone, emotion) goes in the speaker's voice
  phrase on every line, in every clip; the example is now
  `"the man, in a deep British accent (S1)"`.
- Token budget per clip rose from 400 to 500.

## Consequences
- Grok writes blind: it never sees the frames, so a clip that drifts from its prompt
  leaves the next prompt slightly off. The table is editable for exactly that reason.
- The cursor is global, not per video: ⏭ on an older clip still continues from the
  cursor, not from that clip. Use 📋 on a row to move the cursor back.
- The character summary costs part of each clip's prompt and partly undoes the
  `/video-sequence` "don't restate the still" rule, in exchange for consistency across a
  chain of frames. `/video-sequence` got only the voice rule: each of its shots has a
  freshly generated still, so nothing drifts.
