# Add the H3 8-step accelerator LoRAs as `/video-settings` toggles

## Context

`/video-settings` currently exposes five bypassable optimisations — `turbo`, `cache`,
`sage`, `sol`, `spectrum` — each a `MODEL → MODEL` passthrough node marked
`[opt:<key>]` in the three MiniMax H3 templates, deleted per run by
`bypass_optimisation_nodes()` when its checkbox is off. See
`ADR/h3-workflow-consolidation-optimisation-toggles.md`.

A new 8-step accelerator LoRA has arrived in two flavours — `minimax-h3-fl2va-acc-8step`
(matching the `fl2va` UNET used by i2v and t2v) and `minimax-h3-ref2va-acc-8step`
(matching r2v's `ref2va` UNET). It should be selectable the same way the existing 4-step
turbo LoRA is, and become the **new default** accelerator: 8 steps at better quality
instead of 4.

Because a 4-step and an 8-step distillation LoRA chained together produce nonsense, the
accelerator toggles must become **mutually exclusive by step count** — which is the one
genuinely new mechanic here. Everything else follows the existing pattern.

## Decisions taken with the user

- **Mutually exclusive by step count.** Ticking an accelerator unticks any accelerator
  with a *different* step count and moves the steps override to its count. The two 8-step
  variants share a step count and so do **not** exclude each other — they can never both
  be present in one template anyway (fl2va in i2v/t2v, ref2va in r2v), so "both on" simply
  means "use whichever 8-step LoRA this workflow has".
- **New default is 8-step on, turbo off.**
- **Both 8-step rows always shown**, captioned by which workflow kind they apply to.
  Ticking the irrelevant one is a harmless no-op, exactly as the existing five are on a
  non-H3 template.
- LoRA files live in the same `h3\` subfolder as the turbo LoRA; `strength_model` is `1.0`.

## Design: the accelerator group

Descriptors in `static/js/utils.js` gain a `steps` field. A descriptor with `steps` **is**
an accelerator; the field replaces the hardcoded `stateKey === 'optTurbo'` coupling in the
panel and defines the exclusion group.

```js
export const VIDEO_OPTIMIZATIONS = [
  { key: 'turbo',     stateKey: 'optTurbo',     label: 'Turbo 4-step LoRA',                  steps: 4, hint: 'sets Steps to 4' },
  { key: 'accel8fl',  stateKey: 'optAccel8Fl',  label: '8-step accel LoRA (fl2va — i2v/t2v)', steps: 8, hint: 'sets Steps to 8' },
  { key: 'accel8ref', stateKey: 'optAccel8Ref', label: '8-step accel LoRA (ref2va — r2v)',    steps: 8, hint: 'sets Steps to 8' },
  { key: 'cache',    stateKey: 'optCache',    label: 'H3 FirstBlockCache' },
  { key: 'sage',     stateKey: 'optSage',     label: 'Sage attention' },
  { key: 'sol',      stateKey: 'optSol',      label: 'Sol attention' },
  { key: 'spectrum', stateKey: 'optSpectrum', label: 'H3 Spectrum' },
];
```

`TURBO_STEPS`/`BASE_VIDEO_STEPS` stay — `TURBO_STEPS` becomes just the value of turbo's
`steps` field, and `BASE_VIDEO_STEPS` is still what "no accelerator" means.

### Handling old sessions (the `!== false` trap)

Flags are read as "absent means on". A session saved before this change carries an
explicit `optTurbo: true` and **no** 8-step keys — which would read as turbo *and* both
8-step LoRAs on, i.e. exactly the stacked-accelerator graph we must avoid. Shallow-merging
new defaults cannot fix this, since the old explicit `true` wins.

Fix with one pure helper in `utils.js`, used by both the payload builder and the panel:

```js
// The accelerator (an optimisation carrying a `steps` count) that is actually in force.
// Several can read as on only in a pre-upgrade session, where the 8-step keys are absent
// and so read as on alongside an explicitly-chosen turbo; the lowest step count — i.e.
// the one the session actually chose — wins, so an old session renders as it did before.
export function activeAccelerator(vs) { ... }   // returns a descriptor or null
```

`videoOptsPayload` then forces every accelerator outside the winning step-count group to
`false` before sending. From the panel both can never be on at once, so this rule only
ever fires for a restored pre-upgrade session — where it reproduces the old behaviour
exactly.

## Changes

### 1. Workflow templates — `/home/ben/Code/comfy-workflows/` (separate git repo)

⚠ These files hold unquoted placeholders (`<DURATION>`, `<LORA_1_STRENGTH>`) so they are
**not parseable JSON**. Edit them as text; do not round-trip through `json.load`/`dump`.

Insert one `LoraLoaderModelOnly` node in each, directly **after** the `[opt:turbo]` node,
keeping both accelerators adjacent at the head of the chain, and repoint the node that
used to consume turbo. (Chain position is otherwise irrelevant —
`bypass_optimisation_nodes` removes nodes one at a time and rewires each time.)

| File | New node id | `model` ← | Repoint | LoRA name |
|---|---|---|---|---|
| `image2video/minimax-h3-i2v.json` | `105:129` | `["105:125", 0]` | `105:127` (`Load LoRA 1`) `.inputs.model` → `["105:129", 0]` | `h3\minimax-h3-fl2va-acc-8step.safetensors` |
| `text2video/minimax-h3-t2v.json` | `105:129` | `["105:125", 0]` | `105:124` (`[opt:sage]`) `.inputs.model` → `["105:129", 0]` | `h3\minimax-h3-fl2va-acc-8step.safetensors` |
| `text2video/minimax-h3-r2v.json` | `170` | `["149", 0]` | `150` (`[opt:sage]`) `.inputs.model` → `["170", 0]` | `h3\minimax-h3-ref2va-acc-8step.safetensors` |

Ids verified free. Titles: `[opt:accel8fl] Load LoRA (Accel 8-step fl2va)` and
`[opt:accel8ref] Load LoRA (Accel 8-step ref2va)` — `OPT_TITLE_RE`
(`workflow.py:15`) accepts `[a-z0-9_]+`, so both keys match.

Shape to copy (i2v/t2v style — `_meta`, `class_type`, `inputs`, inputs alphabetical;
r2v's appended nodes 165–169 use the same style, so it applies there too). Append at the
end of the dict, as `105:128`/`165`–`169` already are:

```json
  "105:129": {
    "_meta": {
      "title": "[opt:accel8fl] Load LoRA (Accel 8-step fl2va)"
    },
    "class_type": "LoraLoaderModelOnly",
    "inputs": {
      "lora_name": "h3\\minimax-h3-fl2va-acc-8step.safetensors",
      "model": [
        "105:125",
        0
      ],
      "strength_model": 1.0
    }
  },
```

2-space indent, arrays exploded one element per line, trailing newline at EOF.

### 2. Server — one line

`config.py:234` — extend the canonical tuple:
```python
VIDEO_OPTIMIZATIONS = ("turbo", "accel8fl", "accel8ref", "cache", "sage", "sol", "spectrum")
```
Also fix the stale comment above it: the client mirror is in `utils.js`, not `state.js`.

**No other server change.** `workflow.py` (`OPT_TITLE_RE`, `optimisation_nodes`,
`bypass_optimisation_nodes`) is key-agnostic; `_parse_video_opts` (`app.py:1086`) and both
video routes read `VIDEO_OPTIMIZATIONS`; `generation_service.py:559` just forwards the set.
A key absent from a template bypasses nothing, so the fl2va key on r2v is already a no-op.

### 3. Client — `static/js/utils.js`

- Add the two descriptors and the `steps` field (above).
- `DEFAULT_VIDEO_SETTINGS`: `optTurbo: false, optAccel8Fl: true, optAccel8Ref: true`,
  others unchanged.
- Add `activeAccelerator(vs)` and apply its exclusion rule inside `videoOptsPayload`.

Nothing else client-side needs touching: the flags are flat keys riding the existing
object spread, so `saveSession`/`restoreSession`, the `/settings-save` stack, `newChat`
and `state.js` need **no** change — the same property that let `audio` and the original
five be added.

### 4. Client — `static/js/commands.js` (`/video-settings` panel)

- **Steps pre-fill on open** (`:3434-3442`): replace the `work.optTurbo !== false` test
  with `activeAccelerator(work)` — `{ steps: accel ? accel.steps : BASE_VIDEO_STEPS,
  useDefault: !accel }`.
- **Checkbox loop** (`:3639-3650`): replace the `stateKey === 'optTurbo'` special case with
  a generic handler for any descriptor carrying `steps`. On tick: untick every accelerator
  whose `steps` differs (write `work[...] = false` **and** update its registered box in
  `boxes`, so the panel shows the exclusion), set `stepsWork.steps` to this descriptor's
  count, `useDefault = false`, `refreshSteps()`. On untick: if no accelerator remains on,
  fall back to `BASE_VIDEO_STEPS` + `useDefault = true`. An explicit steps edit afterwards
  still wins, as today.
- **Apply summary** (`:3678`): the hardcoded `All <strong>5</strong> optimisations on`
  branch is now unreachable (exclusivity means one accelerator is always off) — replace
  the literal with the live on-count, `VIDEO_OPTIMIZATIONS.length - off.length`.
- **Reset** (`:3686`): `stepsWork.steps = TURBO_STEPS` is wrong once defaults change —
  derive from `activeAccelerator(work)` after the `Object.assign`, same as on open.

The `mkCheckbox` factory and `refreshSteps()` already exist as the single sync points, so
the exclusion just writes through them. The `/settings` summary rows (`:628-636`) are
data-driven and need no edit.

### 5. Tests

Update:
- `tests/test_app_routes.py:849` — the i2v `video_opts` payload sends all keys explicitly;
  add the two. Check `:979` (t2v) likewise.
- `tests/test_workflow.py:325-364` — `_full_chain()` key list and
  `test_every_subset_leaves_the_graph_intact` (`range(32)` → `range(128)`, node-count
  `8 - len(disabled)` → `10 - len(disabled)`). Synthetic fixture, so this is a list edit.
- `tests/js/utils.test.js:752` — asserts the exact five-key payload object.
- `tests/js/utils.test.js:769` — asserts `VIDEO_OPTIMIZATIONS.filter(o => o.hint)` is
  `['turbo']`; becomes the three accelerator keys, better expressed against `steps`.

Add:
- JS: `activeAccelerator` — fresh defaults pick the 8-step group; a pre-upgrade settings
  object (explicit `optTurbo: true`, 8-step keys absent) picks turbo; all accelerators off
  returns `null`.
- JS: `videoOptsPayload` never emits two different step counts as on.
- Python: `bypass_optimisation_nodes` with a key the template does not carry is a no-op
  (covers fl2va-on-r2v) — extend the existing no-op case if one already covers it.

### 6. Docs

- `CLAUDE.md` — the "Video optimisation toggles" section: key list (`turbo cache sage sol
  spectrum` → plus the two), the "five flat booleans"/"all five" wording, the new default,
  and the exclusivity rule. Also the `MODEL` chain sketch.
- `ADR/h3-workflow-consolidation-optimisation-toggles.md` — extend rather than start a new
  ADR; this is the same mechanism gaining a group concept. Record the exclusivity rule and
  the old-session precedence, and update the chain diagram.
- Copy this plan into `plans/` before implementing, per the repo convention.

## Known gaps carried forward

- The ADR's existing gap stands: on a fresh session where `/video-settings` was never
  opened, `currentVideoSteps` is `null`, so the first run uses the template's baked-in 20
  steps rather than 8. Less wrong than it was for the 4-step LoRA, but not fixed here —
  the override is global across all video workflows, which is why the coupling lives in
  the panel.
- The default stack (8-step accel + cache + sage + sol + spectrum) has **never been
  rendered**, same caveat the all-on 4-step stack carries. Needs a test render.

## Verification

1. `node --check static/js/utils.js && node --check static/js/commands.js` — mandatory
   after any JS edit (curly-quote corruption, see CLAUDE.md).
2. `npm run test:js`
3. `python -m pytest tests/ -q` (baseline was 583 passed / 5 skipped, 155 JS tests).
4. Templates still parse after placeholder substitution — the same path
   `list_workflow_variants` uses:
   ```python
   from workflow import fill_placeholders_for_validation
   import json, pathlib
   for p in pathlib.Path('/home/ben/Code/comfy-workflows').rglob('minimax-h3-*.json'):
       json.loads(fill_placeholders_for_validation(p.read_text()))
   ```
5. Graph integrity over the **real** templates, as the ADR did once before: for every
   subset of the seven keys, run `bypass_optimisation_nodes` and assert `BasicGuider.model`
   and `BasicScheduler.model` still trace back to the `UNETLoader`. Script it in the
   scratchpad — it cannot be a committed test, since the templates live in another repo.
6. Manual: open `/video-settings`, confirm the two new rows appear, that ticking Turbo
   unticks both 8-step rows and sets Steps to 4, that ticking either 8-step row unticks
   Turbo and sets Steps to 8, that ticking the *other* 8-step row leaves the first alone,
   and that Reset lands on 8-step-on/turbo-off at Steps 8.
7. Deploy: commit the three JSON files in the `comfy-workflows` repo, and copy them to
   `~/comfy-workflows/{image2video,text2video}/` on `$PROD_SERVER`. Test-render the
   default stack in the ComfyUI editor before trusting it.
8. Release the app change with the `push-to-portainer` skill — pausing for explicit
   approval before the Portainer redeploy.
