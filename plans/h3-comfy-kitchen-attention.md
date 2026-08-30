# Add the `ModelAttentionBackend` optimisation to `/video-settings`

## Context

ComfyUI now ships a built-in `ModelAttentionBackend` node (`comfy_extras.nodes_model_advanced`,
`model/patch`). Confirmed against the live server at `192.168.1.135:8000`:

```
ModelAttentionBackend: inputs { model: MODEL, attention: ["pytorch attention", "comfy kitchen attention"] }
                       output: MODEL
```

It is a plain `MODEL → MODEL` passthrough — exactly the shape of the seven existing H3
speed-for-quality toggles (turbo, accel8fl, accel8ref, cache, sage, sol, spectrum). The
sample graph `/data/Downloads/video_minimax_h3_t2v.json` uses it with
`attention: "comfy kitchen attention"`, wired straight off the `UNETLoader` (`105:6 →
105:123 → BasicScheduler`).

Today it is not reachable from the app at all. The goal is to make it the **eighth**
bypassable optimisation, so it can be ticked per run from `/video-settings` in the same
way as the rest, in all three MiniMax H3 templates.

Decisions taken with the user:

- **Independent checkbox** — no mutual exclusivity with Sage/Sol (the sample graph
  carries neither, but stacking is left to the operator's judgement, as it already is
  for Sage + Sol today).
- **Off by default** — untested against the current default stack, so a fresh session
  keeps rendering exactly as it does now until the box is ticked.
- **Chain position: directly after the `UNETLoader`**, matching the sample graph — ahead
  of the accelerator LoRAs and the other patches.

The whole feature is data-driven (`ADR/h3-workflow-consolidation-optimisation-toggles.md`):
a new optimisation is one descriptor client-side, one key server-side, and one
`[opt:<key>]`-titled node per template. Nothing in the wire format, session save/restore,
`/settings-save` stack, macros or `newChat` needs touching — they all ride the existing
flat `currentVideoSettings` spread.

Chosen key: **`kitchen`** / `optKitchen` / label *Comfy Kitchen attention*.

## Changes

### 1. Workflow templates (`/home/ben/Code/comfy-workflows/`, outside this repo)

Insert one node per template and re-point the current chain head at it. Current heads:

| Template | UNETLoader | Chain head today (`[opt:turbo]`) | New node id |
|---|---|---|---|
| `text2video/minimax-h3-t2v.json` | `105:6` | `105:125` | `105:130` |
| `image2video/minimax-h3-i2v.json` | `105:6` | `105:125` | `105:130` |
| `text2video/minimax-h3-r2v.json` | `127` | `149` | `171` |

New node (t2v/i2v form; r2v uses `"171"` and `["127", 0]`):

```json
"105:130": {
  "inputs": {
    "attention": "comfy kitchen attention",
    "model": ["105:6", 0]
  },
  "class_type": "ModelAttentionBackend",
  "_meta": { "title": "[opt:kitchen] Comfy Kitchen attention" }
}
```

Then change the `[opt:turbo]` node's `inputs.model` from the UNETLoader to the new node
(`["105:6", 0]` → `["105:130", 0]`; r2v: `["127", 0]` → `["171", 0]`).

Resulting chain: `UNET → kitchen → turbo → accel8{fl,ref} → [user LoRA, i2v only] → sage
→ sol → cache → spectrum → BasicGuider/BasicScheduler`.

Notes:
- All three files are **API format** already, which node stripping requires.
- The i2v `UNETLoader` holds a comma-separated model-variant list; `select_model_variant`
  runs after the UI→API conversion and only rewrites that input string, so inserting a
  consumer downstream of it is safe.
- Verified none of the three files already uses id `105:130` / `171`.

### 2. `config.py`

Add `"kitchen"` to `VIDEO_OPTIMIZATIONS` (line ~236). This is the only server-side
change: `_parse_video_opts` (`app.py:1086`) validates against this tuple, and
`bypass_optimisation_nodes` / `optimisation_nodes` (`workflow.py:219,330`) read the key
straight off the `[opt:…]` title, so they need nothing.

### 3. `static/js/utils.js`

- Add to `VIDEO_OPTIMIZATIONS` (line ~255), placed **after `sol`** so the attention
  patches read together in the panel (display order is independent of chain order):
  `{ key: 'kitchen', stateKey: 'optKitchen', label: 'Comfy Kitchen attention' }`
  — no `steps`, so it is not an accelerator and is untouched by `activeAccelerator`
  and the step-count exclusion in `videoOptsPayload`.
- Add `optKitchen: false` to `DEFAULT_VIDEO_SETTINGS` (line ~270).

`commands.js` (panel rendering at ~3656, `/settings` summary at ~632, Apply/Reset
messaging at ~3700) iterates `VIDEO_OPTIMIZATIONS` and needs **no** change. One visible
consequence: because a default-off optimisation now exists, Apply will normally report
`Optimisations bypassed: Comfy Kitchen attention` rather than
`All 8 optimisations on` — which is honest and wanted.

### 4. Tests

- `tests/test_workflow.py:327` — add `"kitchen"` to `CHAIN` (at the **head**, matching
  the new template order); `test_chain_covers_every_declared_optimisation` asserts set
  equality against `config.VIDEO_OPTIMIZATIONS` and will otherwise fail.
- `tests/js/utils.test.js:759` — the `toEqual` in *"an off flag is reported as false"* is
  exhaustive; add `kitchen: false`.
- `tests/js/utils.test.js:747` — *"every non-accelerator optimisation defaults to on"* is
  no longer true. Rewrite it to assert each non-accelerator key's payload value matches
  its `DEFAULT_VIDEO_SETTINGS` flag (`out[key] === (DEFAULT_VIDEO_SETTINGS[stateKey] !==
  false)`), which keeps the real invariant — the payload mirrors the defaults — without
  hard-coding "all on".
- `tests/js/utils.test.js:768` — *"a pre-upgrade settings object…"* also asserts every
  non-accelerator is on for `{duration: 5, optTurbo: true}`; since absent reads as on,
  `kitchen` **is** on there, so this one still passes unchanged. Confirm when running.
- `tests/test_app_routes.py:845` — the `video_opts` dict is a valid subset (unknown keys
  are the only 400), so no change needed; optionally add `"kitchen": True` for coverage.

### 5. Docs

- `CLAUDE.md`, *Video optimisation toggles* section: "seven … optimisations" → eight, add
  **Comfy Kitchen attention** to the list, and note it defaults **off** and is an
  independent third attention patch alongside Sage/Sol (stacking untested).
- `ADR/h3-workflow-consolidation-optimisation-toggles.md`: append a short note recording
  the eighth key, the built-in node it wraps, its chain position and the off default.
- Per the repo convention, save this plan to `plans/h3-comfy-kitchen-attention.md` and
  commit it before implementing.

## Verification

1. `node --check static/js/utils.js && node --check static/js/commands.js`
   (the curly-quote pitfall in `CLAUDE.md`).
2. `npm run test:js` — the three `videoOptsPayload` / `activeAccelerator` blocks above.
3. `./scripts/test-all` (Python unit tests + import tests + Docker container tests),
   or at minimum `python -m pytest tests/test_workflow.py tests/test_app_routes.py -v`.
4. Template sanity, from the repo root — parse each template through the existing
   validation filler and check the marker is found and bypasses cleanly:

   ```bash
   python3 - <<'EOF'
   import json, sys
   from workflow import fill_placeholders_for_validation, optimisation_nodes, bypass_optimisation_nodes
   base = "/home/ben/Code/comfy-workflows/"
   for f in ["text2video/minimax-h3-t2v.json", "text2video/minimax-h3-r2v.json",
             "image2video/minimax-h3-i2v.json"]:
       wf = json.loads(fill_placeholders_for_validation(open(base + f).read()))
       assert "kitchen" in optimisation_nodes(wf), f
       out, removed = bypass_optimisation_nodes(json.loads(json.dumps(wf)), ["kitchen"])
       assert removed == ["kitchen"]
       # the turbo LoRA must fall back onto the UNETLoader
       turbo = optimisation_nodes(out)["turbo"][0]
       up = out[turbo]["inputs"]["model"][0]
       print(f, "ok — turbo now fed by", out[up]["class_type"])
   EOF
   ```

   Expect `UNETLoader` for all three.
5. End-to-end in the app: open `/video-settings`, confirm the new **Comfy Kitchen
   attention** row appears unticked, Apply reports it as bypassed, and a t2v/i2v/r2v run
   completes (this exercises the default path where the node is stripped every run).
   Then tick it and run one t2v generation to confirm the node actually loads — this is
   the only step that proves the new node works with the H3 stack, and it has **never
   been rendered** alongside Sage + Sol + FirstBlockCache + Spectrum.
6. Optional but recommended before trusting it: test-render the combination in the
   ComfyUI editor, as `CLAUDE.md` already advises for the default Spectrum stack.

## Out of scope

- Exposing the node's `"pytorch attention"` value — the toggle is on/off only, matching
  every other optimisation. Switching the backend would need a new control shape.
- Any mutual exclusivity between Kitchen and Sage/Sol.
- Non-H3 templates (LTX, Wan) — a key absent from a template bypasses nothing, so the
  new checkbox is a harmless no-op there.
