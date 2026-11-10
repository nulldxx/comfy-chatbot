# Plan: keep voice details and a character summary in Grok video prompts

## Problem

Voice details in the master prompt (e.g. "a British accent") go missing from the
descriptions `/video-story` and `/video-sequence` get back from Grok. The prompt to Grok:

- bans *any* description of people ("NEVER describe their appearance…"), which Grok
  reads as "drop character traits", accent included;
- allows the voice only as "a short phrase", with the one example "in a deep voice";
- never says a voice detail must be repeated in every clip, though each clip's text is
  sent to the video model on its own.

Separately, `/video-story` clips chain off cut last frames, so the characters drift over a
story. A short character summary in each clip's description would anchor them.

## Changes (`grok.py` prompt text only — no parsing or wire changes)

Both `generate_video_story` and `generate_video_prompt_sequence`:

- New rule: any voice detail in the master prompt (accent, pitch, pace, tone, emotion)
  MUST go in the speaker's voice phrase every time they speak, in every clip/shot.
- Example changed to `"the man, in a deep British accent (S1)"`.
- The appearance ban says "never describe how the scene looks — but always describe how
  people sound".

`/video-story` only:

- Each description **opens with one sentence** summarising how the characters look
  (build, age range, hair, clothing — the same wording in every clip so they stay
  consistent), then the action. Still no setting, room, lighting, style, lens.
- Sentence budget goes from 1–3 to 2–4 to make room for it.

`/video-sequence` keeps its no-appearance rule: each shot has its own freshly generated
still, so there is nothing to drift.

## Tests

Update `tests/test_grok.py` (needle "NEVER describe their appearance") and add assertions
in `tests/test_video_story.py` for the accent/voice rule and the appearance sentence.

## Docs

Add a section to `ADR/video-story.md`; note the voice rule in
`ADR/video-sequence-h3-prompt-format.md`; update the CLAUDE.md `/video-sequence` note.
