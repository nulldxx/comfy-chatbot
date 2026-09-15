# /video-sequence writes MiniMax H3 prompts

## Context

`/video-sequence` was built for Wan 2.2. Grok returned `{prompt, action, audio}` per
shot, and `buildVideoPrompt` sent `"<still-image prompt>. <action>. Audio: <audio>"` to
image2video. MiniMax H3 is now the video model, and its prompt guide
(`MiniMaxAI/MiniMax-H3`, `docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md`) asks for
something different:

```
<alignment instruction>

integrated_multimodal_description: [Shot 1] <style, first-frame anchor, camera, action, (S1) says: <d>[English] …</d>>

overall_soundscape: <ambient / physical / non-verbal sound>

non_diegetic_music: <score | N/A>
```

The old format sent a text-to-image keyword paragraph where the guide wants a first-frame
anchor followed by motion. It had no instruction line and no camera vocabulary, mixed
dialogue into one `audio` string, and had nowhere to put a score.

## Decision

- **Wan format removed**, not kept behind a switch — Wan is obsolete here.
- **`videoMeta` is now `{ description, soundscape, music }`.** Grok writes the fields;
  the client assembles the prompt at send time, because only it knows whether a 🎞️ end
  frame is designated and how long the clip is. The server stores the object untouched
  (`VIDEO_META_KEYS` in `generation_service.py` just names the fields for the copy and
  the replacements loop).
- **One continuous shot per clip.** No `[Shot 2]` cuts — the sequence already supplies
  the cuts between clips, and the guide prefers single shots for FL2VA.
- **One score per sequence.** Grok returns a top-level `music`; `_parse_video_prompts`
  copies it onto every shot (a per-shot `music` wins; blank → `N/A`), so clips cut
  together share a score but each image can still be edited on its own.
- **Grok prompt** (`grok.generate_video_prompt_sequence`) teaches the guide's rules:
  style + shot size first, a restated first-frame anchor (the no-back-reference rule
  still holds, since each shot is rendered alone), action onset → development → result,
  the camera vocabulary with amplitude/speed, `(S1)` speaker IDs with a voice
  description, verbatim dialogue in `<d>[English] …</d>`, the voiceover phrasing, quoted
  on-screen text, and a 1–4 sentence soundscape / 1–3 sentence score with no mood words.
  It includes an adapted I2VA example from the guide. The token budget rose to
  `1024 + count * 800`.
- **Assembly** (`utils.js`):
  - `buildVideoPrompt(base, meta, { audio, lastFrame, duration })` writes the I2VA
    instruction, or the FL2VA one with `duration.toFixed(2)` when an end frame is set.
  - FL2VA also appends `FL2VA_LANDING` ("…settles into the pose, framing, and composition
    established by Picture 2"), because Grok writes each shot knowing only its first frame.
  - An empty soundscape is **omitted** (sending `N/A` would ask for silence); empty music
    is `N/A`, as the guide specifies.
  - With no meta, the still prompt stands in as the description, so plain generations
    still convert.
- **`videoPromptOpts(state, image)`** feeds all three i2v launchers (`chat.js`
  `startImage2VideoFor`, `/i2v` in `commands.js`, the review grid in `grids.js`). Its
  end-frame test matches `runImage2Video`'s, so the text names Picture 2 exactly when the
  payload carries one. The duration is `frames / fps`.
- **Audio checkbox** now means "silent clip": both sound fields are sent as `N/A`.
- **Legacy data is read, not migrated.** `normalizeVideoMeta(meta, base)` is the single
  reader: an old `action` becomes the description prefixed with the still prompt (as the
  old `"<base>. <action>"` did), and old `audio` becomes the soundscape. The metadata
  editor, tooltip, sequence review, Clone button and `/settings` all go through it, and
  saving from the editor writes the new shape.

## Consequences

- Old images keep working, but their dialogue stays inside the soundscape, where H3 won't
  lip-sync it. Re-editing in the metadata editor fixes that.
- The prompt shown in the `Image2video:` user bubble is now a multi-paragraph block.
- `/i2v-set-prompt` / the override prompt stays raw, so the user owns its format.
- **Not covered:**
  - T2VA and L2VA (sequence media is always a still).
  - The R2V guide (`VIDEO_PROMPT_WRITING_GUIDE_ref_en.md`).
  - `/sequence-run` in t2v mode.
- **Untested against the model:** the assembled I2VA and FL2VA prompts still need
  test-rendering through `minimax-h3-i2v.json`.
