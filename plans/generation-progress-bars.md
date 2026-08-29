# Meaningful progress bars during generation

## Context

Today, once a workflow is submitted to ComfyUI the UI freezes on the text
`Queued (ID: 1a2b3c4d…) — generating` with an **indeterminate** CSS marquee
(`@keyframes slide`, `static/css/chat.css:1024-1038`) sliding underneath it. For a
four-hour video render that is the entire feedback the user gets. The information
exists — ComfyUI's own web UI shows a real bar — but this app never reads it.

**Why it isn't there today.** `ComfyServer.poll_status` (`ComfyServer.py:188-263`)
polls `GET /history/<prompt_id>` every 2s, and ComfyUI writes *nothing* to `/history`
until the prompt finishes. Step counts, node execution and queue depth are published
**only** on ComfyUI's WebSocket (`ws://<host>/ws?clientId=…`), and there is no WS
client anywhere in the repo (`requirements.txt` is Flask/Werkzeug/gunicorn/requests).

Two things already in place make this cheap:

1. `ComfyServer.__init__` already generates `self.client_id` and already sends it in
   the `POST /prompt` payload (`ComfyServer.py:47`, `:158`) — it is currently **dead**,
   used by nothing. ComfyUI routes that prompt's progress messages to exactly the
   socket that connected with the same `clientId`, so the correlation key exists.
2. `_run_generation_core` already emits a `{"type":"tick"}` SSE event every ~2s
   (`generation_service.py:325-329`) from `poll_status`'s `"."` heartbeat, and **no
   client code consumes it**. It is a free, already-budgeted slot for the numbers.

Outcome: a determinate bar plus a phase caption in the main generation bubble and in
each sequence-run shot shell, degrading silently to today's marquee whenever the
WebSocket is unavailable.

## Design decisions (confirmed with the user)

- Add `websocket-client` as a dependency rather than hand-rolling RFC6455.
- Bar = **monotonic overall progress** across the graph, driven by node completion
  with the running node's step fraction interpolated into its slice. The precise
  numbers (`Sampling — step 12/20`) live in a caption beneath it.
- Scope: the main generation bubble and the sequence-run shot shells. `/fscheck` and
  `/api/archive` have no ComfyUI step data and keep the marquee — no change.

## Implementation

### 1. Dependency

`requirements.txt` — add `websocket-client==1.8.0` (pure Python, no transitive deps).
`tests/test_imports.py` follows the existing pattern; add the import there so the
Docker image is verified to carry it.

### 2. New module: `comfy_progress.py`

House style favours small focused modules (`seed_store.py`, `idle_lock.py`,
`crypto_key.py`), so the listener goes in its own file rather than swelling
`ComfyServer.py`.

```python
class ProgressListener:
    """Reads one ComfyUI /ws feed and maintains a normalised progress snapshot."""
    def __init__(self, server, client_id, node_titles, total_nodes): ...
    def start(self)            # daemon thread; never raises to the caller
    def bind(self, prompt_id)  # called after submit_workflow returns
    def latest(self) -> dict | None
    def stop(self)
```

- **Connect before submit.** `start()` is called *before* `server.submit_workflow()`
  at `generation_service.py:610` — ComfyUI buffers nothing, so a socket opened after
  the POST misses the opening messages. `bind(prompt_id)` supplies the filter
  afterwards; until bound, every message on our own `clientId` socket is ours anyway.
- **Loop**: `websocket.create_connection(f"ws://{server}/ws?clientId={client_id}")`
  with `settimeout(1.0)`, so `stop_event` is honoured within a second.
  `WebSocketTimeoutException` → continue. Non-`str` frames (ComfyUI's binary
  latent-preview frames) → skip.
- **Message handling** — put this in a pure `_handle(self, msg: dict)` method,
  separate from the socket loop, so tests can drive it without a server:
  - `execution_start` → reset counters.
  - `execution_cached` `{nodes:[…]}` → mark those node ids done immediately.
  - `executing` `{node}` → mark the previous current node done; `node is None`
    means the prompt finished → 100%.
  - `progress` `{value, max, node}` → `current_frac = value / max`.
  - `progress_state` `{nodes: {id: {value, max, state}}}` (newer ComfyUI) → preferred
    when present: `done` = count of `state == "finished"`, current = the running one.
    It is a superset of the two above; handle both so older servers still work.
  - `status` `{status:{exec_info:{queue_remaining}}}` → `queue` field, so the
    pre-execution wait can read "2 ahead in queue" instead of nothing.
  - Ignore any message whose `prompt_id` is set and does not match the bound one.
- **Snapshot** (under a `threading.Lock`):
  `{percent, phase, step, steps, node_index, node_total, queue}`.
  `percent = 100 * (len(done) + current_frac) / total_nodes`, **clamped
  non-decreasing** — ComfyUI can revisit nodes and multi-pass graphs re-run samplers,
  and a bar that goes backwards is worse than no bar.
- **Failure is silent.** Any exception connecting or reading sets a dead flag;
  `latest()` returns `None` thereafter. The job is unaffected — this is telemetry.
- `config.py`: `COMFY_WS_PROGRESS` (default `"1"`) to disable the listener entirely,
  for a ComfyUI behind a proxy that blocks WebSocket upgrades.

### 3. `generation_service.py` — wiring

The whole change is contained in `_run_generation_core`. **`ComfyServer.poll_status`
is not touched at all**; its callback contract stays exactly as it is.

- Declare `listener = None` near the top of the function, *before* the existing
  `progress` closure at line 325 (closures capture the variable, not the value, so
  the later assignment is visible).
- Enrich the existing tick — no new event type, and **no change in event volume**,
  since ticks already fire every 2s:

```python
def progress(msg_str):
    if msg_str == ".":
        snap = listener.latest() if listener else None
        channel.send(json.dumps({"type": "tick", **(snap or {})}))
    else:
        send("progress", message=msg_str)
```

- Around the submit/poll block (`generation_service.py:610-621`): build the listener
  from the API-format `workflow` dict already in scope — `total_nodes = len(workflow)`
  and `node_titles = {id: node.get("_meta", {}).get("title") or node["class_type"]}`.
  **`convert_ui_to_api_format` (`ComfyServer.py:72-137`) does not emit `_meta`**, so
  the `class_type` fallback is the real path for UI-format templates, not a
  formality. Start it, submit, `bind(prompt_id)`, poll, and `stop()` in a `finally`
  so a cancel/retry/timeout can't leak the thread.

Because every ComfyUI job kind funnels through `_run_generation_core`, this single
change lights up t2i, i2v, t2v, face-detail (including the N-variation super job),
upscale, i2i, inpaint, remove and **every shot of a sequence run** at once.

### 4. `app.py` — collapse replayed ticks

`api_progress`'s replay loop (`app.py:1813-1818`) currently re-sends the entire
backlog, which for a long job is thousands of ticks. Ticks are volatile state, not
history: in the replay loop, emit only the **last** tick and drop the rest. This
shrinks the reattach payload and avoids a DOM write per replayed tick — and matters
more now that a tick carries a payload the client acts on.

### 5. Client

**`static/js/utils.js`** — add the pure helpers so they are unit-testable, matching
the existing `clampVideo`/`recomputeVideo` precedent:
- `progressPercent(tick)` → clamped `0..100` or `null` when the tick carries no data.
- `progressCaption(tick)` → `"Sampling — step 12/20 · node 7/23"`, or
  `"Queued — 2 ahead"` when only `queue` is known.

**`static/js/chat.js`**
- `runGeneration` (~line 1977): add a `<div class="progress-caption"></div>` to the
  bubble template; add a `tick` branch to the `es.onmessage` at line 2051 that sets
  `barWrap.classList.add('determinate')`, `bar.style.width`, and the caption text.
  Ticks with no percent are ignored, so the marquee simply stays.
- `openShell` (~line 1666): add the `.progress-bar-wrap` + `.progress-caption` markup
  it currently lacks (it has only `.status-text` and `.dots` today), and remove them
  in `finishShellWithImage` (~1701) alongside the dots.
- `attachSequenceRunStream` (~line 1741): add the `tick` branch **below** the
  `if (!caughtUp) return;` guard at line 1788, so replayed ticks don't try to write
  into a shell that isn't open yet.
- ⚠ Run `node --check static/js/chat.js` after every edit — the Edit tool silently
  converts straight quotes to curly ones in JS (see CLAUDE.md *Known Pitfalls*).

**`static/css/chat.css`** (next to the existing rules at 1024-1038)
```css
.progress-bar-wrap.determinate .progress-bar {
  animation: none; margin-left: 0; transition: width .3s linear;
}
.progress-caption { font-size: 11px; color: #94a3b8; margin-top: 4px; }
```

### 6. Tests

- **`tests/test_comfy_progress.py`** (new) — drive `_handle` with synthetic message
  sequences, no socket. Cover: `execution_cached` counts immediately; `executing`
  advances and `node: null` finishes; `progress` interpolates within a node's slice;
  percent never decreases across a two-pass graph; a foreign `prompt_id` is ignored;
  `progress_state` takes precedence; `latest()` is `None` after a read error.
  Follow the `Mock`-based `_response` helper style in `tests/test_comfy_server.py`.
- **`tests/test_generation_service.py`** — the existing `FakeServer`
  (lines 107-135) has `poll_status(self, *a, **k)` and swallows the callback; extend
  it to invoke `callback(".")` so `_drain(channel)` can assert the enriched tick, and
  that a `None` listener still yields a bare `{"type":"tick"}` (back-compat).
- **`tests/test_jobs_api.py`** — replay collapses multiple ticks to one.
- **`tests/js/utils.test.js`** — `progressPercent` / `progressCaption` edge cases
  (missing fields, `max` of 0, clamping).

## Known limitation, stated up front

Weighting every node equally means the bar is **front-loaded**: a graph of ~25 nodes
where one sampler owns 95% of the wall clock will race to ~70% and then crawl through
the sampler's single slice. The caption carries the honest number throughout, which is
why it is part of the design rather than decoration. A later refinement — cache
observed per-node durations per `(server, workflow_name)` and use them as weights on
the next run — would fix it with measured data rather than guesses; explicitly out of
scope here.

Also out of scope: `/fscheck` and `/api/archive` (no step data), and `/api/jobs` —
the `/jobs` grid still shows only a status label, since the snapshot lives in the job
thread's listener, not on the job record.

## Verification

1. `python -m unittest discover tests/` and `npm run test:js`.
2. `node --check static/js/chat.js` (and `utils.js`) — the curly-quote trap.
3. `./scripts/test-all` for the full Python + Docker container suite.
4. Live, against the real ComfyUI at `COMFY_SERVER`:
   - a plain t2i run — bar advances, caption shows `KSampler — step n/20`;
   - a MiniMax H3 or LTX 2-pass i2v — caption changes phase across samplers and the
     VAE decode, and the bar never goes backwards;
   - a `/sequence-run` of 3 shots — each shot shell gets its own bar, reset per shot;
   - reload mid-render — reattach replays one tick and the bar resumes at the right
     place;
   - `COMFY_WS_PROGRESS=0` (and separately, a bogus `COMFY_SERVER` port) — the
     marquee returns and generation is otherwise unaffected.
5. Deploy with the `push-to-portainer` skill once green, pausing for explicit
   approval before the Portainer redeploy.
