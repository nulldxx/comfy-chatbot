# Bias the progress bar towards the sampler nodes

## Context

64a5fbd gave the generation bubble a determinate progress bar, fed by
`comfy_progress.ProgressListener` reading ComfyUI's WebSocket feed. Its
percentage is `(finished nodes + running node's step fraction) / total nodes` —
**every node counts the same**. That was called out as a known flaw in
`ADR/generation-progress-bars.md`:

> The bar is front-loaded. Every node counts the same, so a graph of ~25 nodes
> where one sampler owns 95% of the wall clock races to ~70% and then crawls
> through a single slice.

So the bar is a poor predictor: it sprints through loaders, conditioning and
resize nodes that cost milliseconds, then sits nearly still for the minutes (or
hours) the KSampler actually takes. This change makes a node's slice of the bar
proportional to a static estimate of its cost, computed once from the submitted
graph, so the bar tracks wall clock much more closely.

Chosen split (agreed with the user): **sampler nodes own 85% of the bar between
them**, VAE decode / video encode nodes get a **5× ordinary node** tier, and
everything else shares what's left.

## Approach

All of the work is in `comfy_progress.py`, plus a one-line change at the call
site. The **wire format is unchanged** — `percent` is simply computed better —
so `utils.js`, `chat.js` and the SSE tick need no changes at all.

### 1. `node_weights_for(workflow)` — new function in `comfy_progress.py`

Sits next to the existing `node_titles_for()` (same shape: takes the
API-format workflow dict, tolerates junk, returns `{node_id: float}` with an
entry for **every** node).

**Tiers**

| Tier | Test | Raw weight |
|---|---|---|
| Sampler | `class_type` matches `/sampler/i` **and** the node has a latent input (`latent_image`, `latent`, `samples`) | share of the sampler budget, by steps (below) |
| Heavy | `class_type` matches `/vaedecode\|vaeencode\|createvideo\|savevideo\|saveanimated/i` | `HEAVY_WEIGHT = 5.0` |
| Ordinary | anything else | `1.0` |

The **latent test is what makes the sampler regex safe**: `KSamplerSelect` and
`SamplerEulerAncestral` merely *name* a sampler and cost nothing, and neither
takes a latent — only the nodes that actually denoise do (`KSampler`,
`KSamplerAdvanced`, `SamplerCustom`, `SamplerCustomAdvanced`). Matching on a
substring rather than an allowlist is deliberate: the templates are authored
outside this repo and pull in custom node packs, so an allowlist would go stale
silently.

**Budget arithmetic** — let `base` = sum of the heavy + ordinary weights. The
samplers get `S = base × SAMPLER_SHARE / (1 − SAMPLER_SHARE)` between them, so
they are exactly `SAMPLER_SHARE` (`0.85`) of the total. `S` is split between the
samplers **in proportion to their step counts**, so a Wan high-noise/low-noise
pair or an LTX two-pass graph divides the budget the way the passes actually
divide the work.

**Steps hint** (`_steps_hint`) — the node's own int `steps` input if it has one
(`KSampler`), else a bounded upstream walk (depth ≤ 3 over API-format link
inputs `["<node_id>", idx]`) for a node carrying an int `steps` — this is how
`SamplerCustomAdvanced` gets its count, via the `BasicScheduler`/`LTXVScheduler`
feeding its `sigmas`. Falls back to `DEFAULT_STEPS = 20`, and is clamped to
1–1000 so a garbage value can't swamp the budget. Because weights are computed
from the **submitted** graph, the `/video-settings` steps override and the
accelerator LoRAs' 4/8-step counts are already baked in.

**Fallback:** a graph with no detected sampler keeps the heavy/ordinary tiers
and skips the share arithmetic — i.e. essentially today's uniform behaviour, so
an unrecognised graph degrades rather than breaking.

### 2. `ProgressListener` — weighted accounting

- Constructor gains an **optional 5th param** `node_weights=None`; `None` means
  uniform, which is byte-for-byte today's behaviour (and keeps the existing
  tests in `tests/test_comfy_progress.py` valid unchanged).
- Store `self._total_weight = sum(weights) or total_nodes`, and a
  `_weight(node_id)` helper defaulting to the **mean** weight for an id not in
  the map (a stray/display id must still advance the bar, not stall it).
- `_recompute()` becomes
  `pct = 100 × (Σ weight(done) + current_frac × weight(current)) / total_weight`.
  The **monotonic clamp, the `_started` gate and the `min(…, 100)` stay exactly
  as they are** — they are what keep multi-pass graphs from going backwards.
- `total_nodes` keeps its two existing jobs untouched: the `node_index`/
  `node_total` caption, and the "is there a graph at all" gate in `start()`.

### 3. Call site

`generation_service.py:625` — pass `node_weights_for(workflow)` alongside the
titles it already passes. The graph there is final (placeholders substituted,
LoRA/optimisation/reference surgery done, model variant selected), which is
exactly what the weights should describe.

## Files

- `comfy_progress.py` — `node_weights_for()`, `_steps_hint()`, the tier
  constants (`SAMPLER_SHARE`, `HEAVY_WEIGHT`, `DEFAULT_STEPS`, the two regexes),
  and weighted `_recompute()`.
- `generation_service.py` — one line at the listener construction (~625).
- `tests/test_comfy_progress.py` — new `TestNodeWeights` covering: sampler
  detected by regex + latent; `KSamplerSelect` **not** counted as a sampler;
  steps read off the node and off an upstream scheduler; two samplers split by
  steps; the 85% total; the heavy tier; no-sampler fallback; junk input. Plus a
  weighted-accounting case on the listener itself (a sampler at 50% of its steps
  lands near the middle of the bar, not near the end).
- `tests/test_generation_service.py:1013` — `StubListener.__init__` needs the new
  arg; add an assertion that the submitted graph's weights reach the listener,
  mirroring `test_listener_sees_the_submitted_graph`.
- `plans/progress-bar-sampler-weighting.md` — this plan, per the repo
  convention, committed before implementation.
- `ADR/generation-progress-bars.md` — the "Consequences" front-loading bullet is
  now the thing that was fixed; rewrite it to describe the weighting and record
  that the shares are an estimate, not measurement.
- `CLAUDE.md` — the **Percent** bullet under "Generation progress bars".

## Verification

1. `python -m pytest tests/test_comfy_progress.py tests/test_generation_service.py -v`
2. `./scripts/test-all` (Python imports + unit tests + Docker container tests).
3. `npm run test:js` — should be untouched, but it is the guard that the wire
   format really didn't move.
4. Live sanity check against the ComfyUI box: run a video generation and watch
   the bubble. Expected shape — a couple of percent while the UNET loads, the
   long middle spent climbing steadily through the sampler with the caption
   tracking `step n/N`, then the last few percent through VAE decode and save.
   Compare with a fast t2i run, where the bar should still complete smoothly
   rather than snapping 0 → 100.
5. Confirm the fallbacks: `COMFY_WS_PROGRESS=0` still leaves the marquee, and a
   workflow whose sampler isn't detected still shows a moving (uniform) bar.
