# Determinate generation progress bars

## Context

Once a workflow was submitted to ComfyUI the UI froze on `Queued (ID: 1a2b3c4d…)
— generating`, with an indeterminate CSS marquee sliding underneath. For a
four-hour video render that was the entire feedback available.

The data existed but was never read. `ComfyServer.poll_status` polls
`GET /history/<prompt_id>`, and ComfyUI writes **nothing** there until the prompt
finishes; step counts, node execution and queue depth are published only on its
WebSocket (`ws://<host>/ws?clientId=…`). Two things already in the code made
closing the gap cheap:

- `ComfyServer` already generated a `client_id` and already sent it with every
  `POST /prompt` — dead code, but exactly the key ComfyUI uses to route a
  prompt's progress messages to a socket.
- `_run_generation_core` already emitted `{"type":"tick"}` every ~2s from
  `poll_status`'s `"."` heartbeat, and **no client code consumed it**.

## Decision

Read the WebSocket on a per-job thread, reduce it to a small snapshot, and let
the snapshot ride the tick that was already being sent. **The bar costs no extra
SSE events**, and `poll_status` was not modified at all.

### `comfy_progress.py`

`ProgressListener(server, client_id, node_titles, total_nodes)` — `start()`,
`bind(prompt_id)`, `latest()`, `stop()`. `_handle(msg)` is kept free of socket
concerns so the whole message vocabulary is testable without a server.

- **Connect before submit.** ComfyUI buffers nothing, so a socket opened after
  the `POST /prompt` misses the opening messages. `bind()` supplies the prompt
  filter once the submit returns; messages with no `prompt_id` are accepted,
  since on our own `clientId` socket they are ours and older builds omit it.
- **Percent** = `(finished nodes + running node's step fraction) / total nodes`,
  **clamped non-decreasing**. Multi-pass graphs (LTX, Wan high/low-noise) re-run
  their samplers, and a bar that goes backwards is worse than no bar.
- **`progress_state` is not a full picture of the graph.** Observed against
  ComfyUI 0.34.2, it lists only nodes it holds progress records for — a node that
  arrived via `execution_cached` never appears in it. Its finished nodes are
  therefore **unioned** into what is already known; replacing the set made
  progress drop back on every such message (visible only as a frozen bar, since
  the monotonic clamp swallowed the regression).
- **`_started` flag.** Distinguishes "0% and running" from "nothing heard yet",
  so a message we understood but that carried no state — or a malformed one —
  can't publish a phantom 0% and flip the client's bar to determinate-and-empty.
- **Failure is silent and total.** No `websocket` module, a refused upgrade, a
  read error: `latest()` returns `None` and the UI keeps today's marquee. The
  construction is wrapped in `try/except` at the call site too — this is
  telemetry and must never cost the user an image.
- `COMFY_WS_PROGRESS` (`config.py`, default on) disables it outright.

### Wire format

The tick gains optional fields — `percent`, `phase`, `step`, `steps`,
`node_index`, `node_total`, `queue`. A bare `{"type":"tick"}` is exactly what a
run with no listener sends, so old clients and non-ComfyUI jobs are unaffected.

`api_progress`'s **replay collapses stale ticks to the newest one**: a tick is
volatile state, not history, and a long render would otherwise replay thousands
of superseded events (and, now, thousands of pointless DOM writes) to a
reattaching client.

### Client

`progressPercent` / `progressCaption` are pure functions in `utils.js` (matching
the `clampVideo`/`recomputeVideo` precedent) and unit-tested. `applyProgressTick`
in `chat.js` drives both surfaces; `.determinate` on `.progress-bar-wrap` kills
the marquee animation and hands width over to the percentage.

The sequence-run shot shells (`openShell`) gained the `.progress-bar-wrap` they
never had. `pauseShellOnFailure` calls `resetProgress` — the shell stays open for
⟳, and leaving the bar parked at the failed attempt's percentage would be a lie.

## Consequences

- One new dependency, `websocket-client==1.8.0` (pure Python, no transitive
  deps).
- **The bar is front-loaded.** Every node counts the same, so a graph of ~25
  nodes where one sampler owns 95% of the wall clock races to ~70% and then
  crawls through a single slice. The caption (`Sampling — step 12/20 · node
  7/23`) carries the honest number throughout, which is why it is part of the
  design and not decoration. Caching observed per-node durations per
  `(server, workflow_name)` and using them as weights would fix this with
  measured data rather than guesses — deliberately not done here.
- Because every ComfyUI job kind funnels through `_run_generation_core`, this
  covers t2i, i2v, t2v, face-detail (including the N-variation super job),
  upscale, i2i, inpaint, remove and every shot of a sequence run.
- `/fscheck` and `/api/archive` have no step data and keep the marquee. `/jobs`
  still shows only a status label — the snapshot lives in the job thread's
  listener, not on the job record.
- Verified live against ComfyUI 0.34.2: handshake, `status`/`execution_start`/
  `execution_cached`/`progress_state`/`executing` handling, 100% on completion
  and clean thread shutdown. The **phase caption during a long sampler run** is
  covered by unit tests against the observed payload shape but has not been
  eyeballed on a real render — worth a glance on first use.
