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

## Moved (2026-09-16)

All six moved to one server-side batch job, `/api/batch-run` — see
`ADR/server-side-batch-runs.md`: `/face-detail-auto` on plain prompts, `/i2v <N>`,
`/iterations`, `/multi-prompt`, `/face-detail <N>` / `/face-detail-session`, and
`/t2i-workflow-iterate`.

## Still client-side, not yet surveyed for moving

- `/upscale <N>` — a promise chain of one upscale job per image (`commands.js`). It
  would be one more batch step kind.

## Should stay client-side

Macros — `#name` (`chat.js:1542`), the default macro (`commands.js:1184`,
`runDefaultMacroOnImage`) and the grid's macro button. A macro step can be **any** slash
command, and those read browser state: `sessionImages`, picker bubbles, the `/references`
table. Moving them would mean running the command parser on the server.

## Already server-side (no work needed)

- `/sequence`, `/video-sequence` and their auto passes — `run_sequence_run`.
- The batch commands above — `run_batch_run`.
- `/face-detail-super` — one request carrying a `count`, not N requests.
- `/video-splice` — `POST /api/composite-videos`.
- `/archive-*`, `/fscheck`, `/archive-explore`.
