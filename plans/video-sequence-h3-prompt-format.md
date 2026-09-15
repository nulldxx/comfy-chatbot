# /video-sequence → MiniMax H3 prompt format

## Context

`/video-sequence` was built for Wan 2.2. Grok returns `{prompt, action, audio}` per shot and
`buildVideoPrompt` (`static/js/utils.js:176`) sends `"<still-image prompt>. <action>. Audio: <audio>"`
to image2video. H3 has become the video model, and its guide
(`MiniMaxAI/MiniMax-H3 docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md`) expects something quite different:

```
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] Live-action, cinematic, <first-frame anchor> … <camera motion> … (S1) says: <d>[English] exact words</d> …

overall_soundscape: <1–4 sentences: ambient, physical and non-verbal sound only>

non_diegetic_music: <1–3 sentences: instruments, tempo, dynamics | N/A>
```

The current format breaks the guide in these ways:
- It sends a text-to-image keyword paragraph where the guide wants a compact first-frame anchor followed by motion.
- It has no alignment instruction line.
- Dialogue and ambient sound share one `audio` string. The guide wants dialogue inline with speaker IDs and `<d>` tags, and ambient sound in its own field.
- It has no field for music.
- It has no camera-motion vocabulary.
- When an end frame is set, FL2VA needs its own instruction line with the clip duration.

**Decisions (confirmed):**
- Remove the Wan format entirely.
- The Audio checkbox now means silence: `overall_soundscape: N/A` and `non_diegetic_music: N/A`.
- Grok writes one music line per sequence, copied to each shot and still editable per image.
- Each clip is one continuous shot, with no in-clip `[Shot 2]` cuts.

## Design

**New per-image `videoMeta` shape:** `{ description, soundscape, music }`, replacing `{ action, audio }`.
- The server stores this object as-is (`append_session_image`), so it needs no schema change. Only producers and readers change.
- Grok writes the structured fields, and the client assembles the final prompt at send time. Only the client knows whether an end frame is set and how long the clip is.
- The client does not rewrite old sessions. A pure `normalizeVideoMeta(meta, base)` in `utils.js` reads both shapes:
  - Old `action` becomes `description`, with `base` (the still prompt) in front, as the old format had it.
  - Old `audio` becomes `soundscape`. This is an accepted loss: old dialogue stays in soundscape.

### 1. Grok prompt (`grok.py` `generate_video_prompt_sequence`, `_parse_video_prompts`)
Rewrite the user prompt so Grok returns `{"music": "...", "prompts": [{"prompt", "description", "soundscape"}]}`:
- **`prompt`**: keeps the current still-image rules unchanged, including the no-back-references block.
- **`description`**: one English paragraph for a single continuous shot, about 5 seconds long.
  - Leave out `[Shot 1]` and the instruction line; the code adds both.
  - Open with the style (Live-action / cinematic / 2D-animated…) and shot size.
  - Follow with a compact first-frame anchor: subject appearance, clothing, setting and spatial layout, consistent with that shot's `prompt`.
  - Then describe action onset → continuous development → result or reaction.
  - Write camera motion as a natural sentence using the guide's vocabulary (push in/pull out, pan, truck, tilt, pedestal, arc, tracking, static, zoom, shake, POV, roll), adding "with small/large amplitude" and "at slow/fast speed" where they matter.
  - Introduce speakers as `(S1)`, `(S2)` with a voice description (age, gender, pitch, timbre).
  - Put dialogue in `<d>[English] exact words</d>` (verbatim, short enough for the clip). Voiceover uses "says in an off-screen voiceover … while his lips remain completely closed". On-screen text goes in double quotes.
  - Apply the same self-contained, no-back-reference rule as `prompt`; speaker IDs restart at S1 in every shot.
- **`soundscape`**: 1–4 sentences of ambient, physical and non-verbal sound. It must not repeat dialogue or music, must not use mood words, and must never be N/A.
- **top-level `music`**: 1–3 sentences on instrumentation, tempo, rhythm and dynamics, without mood words. Use `N/A` if the master prompt implies no score.
- Include the guide's I2VA Case 2 body as a worked example of the three fields.
- Raise the token budget (`1024 + count * 800`); descriptions are longer than the old action lines.
- `_parse_video_prompts` returns `[{prompt, description, soundscape, music}]`:
  - The top-level `music` is copied into every shot; a per-item `music` wins if present.
  - A missing or blank value becomes `"N/A"`.

### 2. Server run (`generation_service.py` `run_sequence_run`, ~l.920 and ~l.955)
- Swap the `("prompt","action","audio")` key tuples for `("prompt","description","soundscape","music")` in the item copy, the replacements loop and the `video_meta` dict.
- Nothing else changes. Events and the recording already pass `video_meta` through untouched.

### 3. Prompt assembly (`static/js/utils.js`)
- Replace `buildVideoPrompt(base, meta, includeAudio)` with `buildVideoPrompt(base, meta, { audio = true, lastFrame = false, duration })`:
  - `description = normalizeVideoMeta(meta, base).description || base`. A plain image with only a still prompt still works.
  - Instruction line:
    - With no end frame, use the I2VA line verbatim.
    - With `lastFrame`, use the FL2VA line: `… Picture 2 (from Shot 1) aligns with the ${duration.toFixed(2)}-second mark …`.
  - Emit `integrated_multimodal_description: [Shot 1] <description>`, a blank line, then the other two fields.
  - If `audio` is false, both sound fields are `N/A`. Otherwise an empty soundscape line is omitted and empty music becomes `N/A`, as the guide requires.
- Add `videoPromptOpts(state, image)`, which returns `{ audio, lastFrame, duration }`:
  - `lastFrame` is `state.lastFrameUrl && state.lastFrameUrl !== image`, the same test `runImage2Video` uses at `chat.js:872`.
  - `duration` is `frames / fps` from `currentVideoSettings`.
  - It takes `state` as an argument because `state.js` imports `utils.js`.
- `i2vTooltip(meta)` shows a truncated description, read through `normalizeVideoMeta`.
- `state.image2videoOverridePrompt` stays raw and is never wrapped.

### 4. Callers and UI
- **The three i2v call sites** switch to `buildVideoPrompt(base, meta, videoPromptOpts(state, img))`:
  - `chat.js` `startImage2VideoFor` (~l.848)
  - `commands.js` `/i2v` (~l.1973)
  - `grids.js` (~l.326)
  - Their "no prompt" guard accepts `meta.description` as well as `meta.action`.
- **`chat.js` SSE `prompts` handler** (~l.1796): `lastSequence.items` carries the new keys.
- **`grids.js` sequence review:** the rows show Description / Soundscape / Music. The ▶ button passes the new `videoMeta`.
- **`chat.js` `openVideoMetaEditor`:**
  - Fields become Prompt, Description (textarea), Soundscape (textarea) and Music.
  - `commitMeta` writes the new shape; if every field is empty, it deletes the meta.
  - The editor pre-fills its fields through `normalizeVideoMeta`, so old images open correctly.
  - The Clone button and `runImage2Video`'s `lastVideoMeta` record use the new keys.
- **`commands.js`:**
  - The `/settings` "Last video metadata" row shows the new keys.
  - The `/video-settings` Audio checkbox label/tooltip changes to "off = silent (soundscape & music N/A)".
  - Update the `/video-sequence` help text (~l.2392) and `autocomplete.js:96`.
- Run `node --check` on every edited JS file (curly-quote pitfall).

### 5. Docs (project rules)
- Before implementing, commit this plan as `plans/video-sequence-h3-prompt-format.md`.
- After implementing, write `ADR/video-sequence-h3-prompt-format.md`.
- In `CLAUDE.md`, rewrite the "Audio toggle" bullet and add a short `/video-sequence` H3-format section: shape, legacy normalisation, and the I2VA/FL2VA instruction choice.

## Out of scope
- T2VA and L2VA: sequence media is always a still that becomes an i2v clip.
- The R2V guide (`VIDEO_PROMPT_WRITING_GUIDE_ref_en.md`).
- `/sequence-run` in t2v mode.
- In-clip cuts.

## Verification
- `python -m pytest tests/test_grok.py tests/test_generation_service.py -v`:
  - Update the shot fixtures at `test_generation_service.py` ~l.298/359 to the new keys.
  - Add parser tests for music propagation, per-item override, blank → `N/A`, and non-dict/blank-prompt skipping.
  - Assert the replacements reach `description`, `soundscape` and `music`.
- `npm run test:js`: rewrite the `buildVideoPrompt` block in `tests/js/utils.test.js` (~l.558) to cover:
  - the I2VA layout, exactly
  - FL2VA with `duration` 5 → `5.00-second mark`
  - `audio:false` → both N/A
  - empty soundscape omitted and empty music → N/A
  - a legacy `{action, audio}` migrated
  - no meta → base used as the description

  Also update the `i2vTooltip` tests and add `videoPromptOpts` tests.
- `./scripts/test-all`.
- Manual check:
  1. Run `/iterations 2` then `/video-sequence a busker singing on a rainy bridge`.
  2. Check `/sequence-review` shows the three fields.
  3. Press 🎬 on a still and confirm the user bubble shows the exact I2VA prompt.
  4. Designate a 🎞️ end frame and confirm the FL2VA line with the right duration.
  5. Untick Audio and confirm both sound fields read N/A.
  6. Reload an old chat with `{action, audio}` meta and confirm the editor and 🎬 still work.
  7. Test-render one I2VA and one FL2VA prompt through `minimax-h3-i2v.json`.
