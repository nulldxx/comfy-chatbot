# Commands that still loop client-side

A survey (2026-09-15) of which multi-step commands are driven from the browser and
which are driven from the job thread, and which of the former ought to move.

## What "client-side" costs

Every individual ComfyUI job already runs on a server thread (`start_generation_job`),
so a single generation survives the tab closing. What lives in the browser is the code
that decides **what runs next**: close the tab mid-run and the current job finishes,
but nothing after it starts.

There is a second, quieter cost. Only sequence runs record their results into the chat
session from the server, via `append_image_to_recording`
(`generation_service.py:1236`, called from `run_sequence_run`'s `record`). Every other
command relies on the browser to write the session. So a job that *does* finish after
the tab is gone leaves its image in the gallery but never in the chat — which is also
why a client-side chain can't simply be left to run headless as-is.

## Should move

Ordered by how much the move is worth.

| Command | Loop lives in | Why it matters |
|---|---|---|
| `/face-detail-auto` on ordinary prompts | `chat.js:2296` (the `image` handler in `runGeneration`) | The server version already exists inside `run_sequence_run`, so this is mostly reuse. Today the pass only covers sequence-run shots server-side; a plain prompt's pass is queued in the browser. |
| `/i2v <N>` | `commands.js:1976` | Videos take minutes each, so `/i2v 10` is exactly the run you start and walk away from. |
| `/iterations` (N copies of a plain prompt, or of a t2v prompt while `/t2v` is on) | `chat.js:1586` | A large N stops after the current job if the tab goes. |
| `/multi-prompt` | `commands.js:1574` | A pasted list of prompts, run one at a time from the browser. |
| `/face-detail <N>`, `/face-detail-session` | `commands.js:2291`, `commands.js:2263` | Promise chains of one face-detail job per image. |
| `/t2i-workflow-iterate` | `commands.js:2202` | One prompt run through each ticked workflow in turn. |

## Should stay client-side

Macros — `#name` (`chat.js:1542`), the default macro (`commands.js:1184`,
`runDefaultMacroOnImage`) and the grid's macro button. A macro step can be **any** slash
command, and those read browser state: `sessionImages`, picker bubbles, the `/references`
table. Moving them would mean running the command parser on the server.

## Already server-side (no work needed)

- `/sequence`, `/video-sequence` and their auto passes — `run_sequence_run`.
- `/face-detail-super` — one request carrying a `count`, not N requests.
- `/video-splice` — `POST /api/composite-videos`.
- `/archive-*`, `/fscheck`, `/archive-explore`.

## Suggested shape

One general server-side batch job rather than six endpoints: a list of steps like
`{kind, prompt, image?, workflow?}` run in order on a single thread. It would reuse
`run_sequence_run`'s session recording, per-stage retry, cancel handling and its auto
face-detail stage; each command above would just build the list and post it. The client
would render progress the way it already does for a sequence run.

Two things to settle when this is picked up:

- **Settings snapshotting.** As with `/video-sequence-auto`, the steps must carry the
  settings they were requested with (workflow, denoise, steps, video settings,
  optimisations, references) and be validated with the ordinary parsers so a bad
  request is a 400 before any job starts.
- **Prompt derivation.** `/face-detail <N>` and `/i2v <N>` derive their prompts from
  `state.imagePrompts` / `state.imageVideoMeta` in the browser. The server equivalents
  live in `prompt_builders.py` (ports of `deriveFaceDetailPrompt` / `buildVideoPrompt` /
  `normalizeVideoMeta` / `applyReplacements`) — change them together with `utils.js`,
  as `tests/test_prompt_builders.py` mirrors the JS cases.
