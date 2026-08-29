# Consolidate the MiniMax H3 workflows behind optimisation checkboxes

## Context

`/data/Downloads/h3` currently holds **8** MiniMax H3 templates that differ only in which
speed/quality optimisations are baked into the model chain:

| | turbo 4-step LoRA | H3 cache | Sage attn | Sol attn | Spectrum | steps |
|---|---|---|---|---|---|---|
| `image2video/minimax-h3-i2v-26-step.json` | – | ✓ | ✓ | ✓ | – | 26 |
| `image2video/minimax-h3-i2v-turbo-4step.json` | ✓ | ✓ | ✓ | ✓ | – | 4 |
| `text2video/minimax-h3-t2v-26step.json` | – | ✓ | ✓ | ✓ | – | 26 |
| `text2video/minimax-h3-t2v-turbo-4step.json` | ✓ | ✓ | ✓ | ✓ | – | 4 |
| `text2video/minimax-h3-t2v-hq-spectrum-32step.json` | – | – | – | – | ✓ | 32 |
| `text2video/minimax-h3-r2v-26-step.json` | – | – | – | – | – | 26 |
| `text2video/minimax-h3-r2v-fast-26-step.json` | – | ✓ | ✓ | ✓ | – | 26 |
| `text2video/minimax-h3-r2v-turbo-4step.json` | ✓ | ✓ | ✓ | ✓ | – | 4 |

Every one of these optimisations is a `MODEL → MODEL` passthrough node sitting in one
chain between `UNETLoader` and the guider/scheduler, so the whole matrix collapses to
**three** templates — one per generation kind — with the optimisations toggled per run.
Adding a sixth optimisation today means authoring another 2ⁿ files; after this change it
means adding one node and one checkbox.

The three keep separate files because they are genuinely different graphs, not
optimisation variants: I2V and T2V share the `fl2va` UNET but I2V has the
`LoadImage → resize → first_frame` sub-chain, and R2V uses a different UNET
(`minimax_h3_ref2va_…`) with the 15-slot `MiniMaxH3ReferenceToVideo` node.

Outcome: three templates, five checkboxes in `/video-settings`, all five on by default
as a fast preview mode.

## Decisions taken

- **All five optimisations default to on.** ⚠ Turbo + cache + Spectrum has never been
  rendered — Spectrum only ever appears in the 32-step HQ file, where it *replaces*
  FirstBlockCache and feeds the guider directly. Test-render the default combination in
  the ComfyUI editor before trusting it.
- **Steps stay a `/video-settings` concern.** Templates bake `steps: 20`. Ticking Turbo
  turns the steps override on and sets it to 4; unticking it re-ticks *Use workflow
  default* (back to 20). No steps logic anywhere else.
- **Files replaced in place.** The 8 originals move to `/data/Downloads/h3/.superseded/`
  (nothing there is under git).

## Part 1 — Author the three templates

Each is derived from the `-turbo-4step` variant of its kind, which already carries four
of the five optimisations in a consistent order:

```
UNETLoader → [opt:turbo] LoRA → [opt:sage] → [opt:sol] → [opt:cache] → [opt:spectrum] → BasicGuider.model
                                                                                      → BasicScheduler.model
```

Spectrum (`SpectrumApplyMiniMaxH3`, lifted from `minimax-h3-t2v-hq-spectrum-32step.json`
node `105:122` with its parameters unchanged) goes **outermost** so it wraps the rest.

**Marking.** A node is an optimisation iff its `_meta.title` starts with `[opt:<key>]`,
key ∈ `turbo cache sage sol spectrum`. Titles survive a ComfyUI re-export, and the marker
disambiguates what `class_type` cannot: `minimax-h3-i2v-turbo-4step.json` has **two**
`LoraLoaderModelOnly` nodes — `105:125` (the turbo LoRA, which is an optimisation) and
`105:127` (the `<LORA_1_NAME>` user slot, which must never be touched here; it is already
handled by `strip_lora_nodes`).

### `image2video/minimax-h3-i2v.json` — from `minimax-h3-i2v-turbo-4step.json`
- Mark `105:125` turbo, `105:124` sage, `105:126` sol, `105:122` cache; add the spectrum node.
- **Delete `105:123` `MiniMaxH3MemoryEfficientSageAttentionPatch`** — it has no inputs and
  no consumers in either turbo file. It is dead weight, not a fifth optimisation.
- **Add `<DURATION>` and `<FPS>`**, which this graph lacks (duration is hardcoded `5` on
  `105:111`, fps hardcoded `24` on `105:107`'s expression and on `105:91` `CreateVideo`).
  Copy the wiring verbatim from `minimax-h3-t2v-26step.json`, which already has both.
- `BasicScheduler` (`105:9`) `steps: 20`.

### `text2video/minimax-h3-t2v.json` — from `minimax-h3-t2v-turbo-4step.json`
Same treatment; it already has the full placeholder set. Delete the dangling `105:123`,
add spectrum, `steps: 20`.

### `text2video/minimax-h3-r2v.json` — from `minimax-h3-r2v-turbo-4step.json`
- Mark `149` turbo, `150` sage, `151` sol, `152` cache; add spectrum; `124` `steps: 20`.
- **Add `<FPS>`**: node `131`'s expression `round(a * 24)` → `round(a * <FPS>)`, and
  `130` `CreateVideo` `fps: 24` → `<FPS>`.
- **Add `<VIDEO_WIDTH>`/`<VIDEO_HEIGHT>`**, replacing the `ResolutionSelector` (`115`)
  with the two-`PrimitiveInt` + two snap-to-32 `ComfyMathExpression` pattern from
  `minimax-h3-t2v-26step.json` (nodes `115/116/118/119`). Fresh IDs needed — `115` and
  `149`–`164` are taken. Preserve the output-index convention exactly: the snap nodes are
  read at index **1** (`["118", 1]`), the primitives at **0**.
- Keep all 15 reference slots and the `<REFERENCE_*>` placeholders untouched.

Placeholder parity after this: I2V gets `<PROMPT> <INPUT_IMAGE> <LORA_1_*> <DURATION>
<FPS> <VIDEO_WIDTH> <VIDEO_HEIGHT>`; T2V the same minus image/LoRA; R2V the same minus
image/LoRA plus the reference slots. No new placeholder tokens are introduced, so
`fill_placeholders_for_validation` (`workflow.py:288`) needs no change.

## Part 2 — Bypass plumbing

### `workflow.py` — new stripper, beside `drop_node_output_links` (`workflow.py:174`)

```python
OPT_TITLE_RE = re.compile(r"^\s*\[opt:([a-z0-9_]+)\]")

def optimisation_nodes(workflow):   # -> {key: [node_id, ...]}
def bypass_optimisation_nodes(workflow, disabled):  # -> (workflow, removed)
```

`bypass_optimisation_nodes` removes each marked node whose key is in `disabled` and
rewires its MODEL passthrough, reusing `_rewire_references(workflow, nid, {0: inputs["model"]})`
(`workflow.py:216`) exactly as `strip_lora_nodes` (`workflow.py:124`) does. Removing one
node at a time and rewiring after each handles **chained** removals for free: dropping
sage first repoints sol's `model` at sage's upstream, so dropping sol next is still correct.

Follow `_apply_track_drop`'s house rule of failing loudly over submitting a wrong graph:
raise if a marked node has no `model` input, rather than leaving a dangling reference.

### `config.py`

`VIDEO_OPTIMIZATIONS = ("turbo", "cache", "sage", "sol", "spectrum")` — the canonical key
list, so client and server quote one set (same reasoning as `REFERENCE_MAX_FILES`).

### `app.py`

New `_parse_video_opts(data)` beside `_parse_video_settings` (`app.py:1029`): reads the
`video_opts` object, rejects unknown keys against `VIDEO_OPTIMIZATIONS` with a 400, and
returns the set of **disabled** keys. **Absent means nothing disabled**, so an old client
or a non-video job runs the template exactly as authored. Forward
`disabled_optimizations=…` from both `start_generation_job` calls — `api_image2video`
(`app.py:1380`) and `api_text2video` (`app.py:1444`).

Do **not** add it to the video-detection tuple at `generation_service.py:691`.

### `generation_service.py`

Add `disabled_optimizations=None` to `_run_generation_core` (`app.py`-facing signature at
`generation_service.py:300`). Call the stripper in the node-surgery block, immediately
after the `_apply_track_drop` loop (`generation_service.py:545-548`) and before the
UI→API conversion, with a `send("progress", …)` line naming the bypassed keys, matching
the neighbouring strip messages.

Like every other stripper here this assumes API format — already the documented
requirement for these templates.

## Part 3 — `/video-settings` UI

### State — five flat booleans, not a nested object

Add `optTurbo optCache optSage optSol optSpectrum`, all `true`, to
`DEFAULT_VIDEO_SETTINGS` (`static/js/utils.js:206`), read everywhere with the existing
`!== false` idiom so an absent key means on.

**Flat, deliberately.** Both restore paths shallow-merge — `{ ...DEFAULT_VIDEO_SETTINGS,
...s.videoSettings }` (`chat.js:1445`, `commands.js:2994`) — and Apply does
`state.currentVideoSettings = { ...work }` (`commands.js:3536`). A nested `opts` object
would (a) be replaced wholesale by an old snapshot, losing defaults for keys added later,
and (b) be shared by reference between `work` and state, leaking panel edits before Apply.
Flat keys ride the existing spread for free, so **`saveSession`, `restoreSession`,
`/settings-save`, `/settings-restore` and `newChat` need no changes at all** — exactly as
when `audio` was added.

Also add to `static/js/state.js`, next to `VIDEO_RESOLUTION_PRESETS` (`state.js:15`), a
`VIDEO_OPTIMIZATIONS` array of `{ key, stateKey, label, hint }` driving both the checkbox
rows and the wire payload, mirroring `config.py`'s list with a pointer comment.

### Panel — `commands.js:3331-3565`

Add an "Optimisations" block after the steps rows, built from `VIDEO_OPTIMIZATIONS` via a
small `mkCheckbox(stateKey, labelHtml)` factory cloning the `audioRow`/`audioBox` block
(`commands.js:3464-3474`); refactor the Audio row through the same factory.

Extract a `refreshSteps()` from the inline slider/input sync in `onStepsEdit`
(`commands.js:3494-3502`) that also syncs `defaultStepsBox`, and use it from three places:

1. **Turbo ticked** → `stepsWork = { steps: 4, useDefault: false }; refreshSteps()`.
2. **Turbo unticked** → `stepsWork = { steps: 20, useDefault: true }; refreshSteps()`.
3. **Reset** (`commands.js:3545`), which currently hand-syncs each control — the report of
   record for the existing bug that a new checkbox forgotten here shows a stale tick.
   Reset must also re-sync all five new boxes.

Also update: the Apply confirmation string (`commands.js:3542`), the `/settings` status row
(`commands.js:566-577`), and the `/video-settings` help entry (`commands.js:2291`).

### Wire — `chat.js:2027`

Add a `video_opts` key to the existing `image2video || text2video` spread, built by a new
pure helper `videoOptsPayload(vs)` in `utils.js` (pure so it is unit-testable — the jest
env is `node` with no jsdom, so the panel itself cannot be tested).

Client-side masking is *not* wanted here: unlike `referencesForRun`, every flag must reach
the server, because "on" and "absent" are the same thing on the wire.

## Known consequence

On a **fresh session where `/video-settings` has never been opened**, `currentVideoSteps`
is `null` and Turbo is on by default, so the first run loads the turbo LoRA at the
template's baked-in 20 steps — slower than intended, not broken. Opening
`/video-settings` and pressing Apply once fixes it for the session. The panel mitigates
this by pre-filling the override to 4 when it opens with Turbo on and no explicit
override set.

Raising `currentVideoSteps`' default to 4 instead was rejected: it is global across all
video workflows, so it would silently drop the Wan 2.2 and LTX 2.3 templates — which have
no `[opt:turbo]` node to untick — to 4 steps.

## Files

| File | Change |
|---|---|
| `/data/Downloads/h3/image2video/minimax-h3-i2v.json` | new (8 originals → `.superseded/`) |
| `/data/Downloads/h3/text2video/minimax-h3-t2v.json` | new |
| `/data/Downloads/h3/text2video/minimax-h3-r2v.json` | new |
| `workflow.py` | `OPT_TITLE_RE`, `optimisation_nodes`, `bypass_optimisation_nodes` |
| `config.py` | `VIDEO_OPTIMIZATIONS` |
| `app.py` | `_parse_video_opts`, forward from both video routes |
| `generation_service.py` | kwarg + stripper call in the node-surgery block |
| `static/js/utils.js` | 5 keys on `DEFAULT_VIDEO_SETTINGS`, `videoOptsPayload` |
| `static/js/state.js` | `VIDEO_OPTIMIZATIONS` descriptor array |
| `static/js/commands.js` | checkbox block, `refreshSteps`, Reset, Apply/`/settings`/help text |
| `static/js/chat.js` | `video_opts` on the video POST body |
| `CLAUDE.md` | new section; update the `/video-settings` and workflow-parameter tables |
| `ADR/` | rewrite this plan as an ADR once implemented |

## Verification

1. **Templates parse and stay whole, under every combination** — a new
   `tests/test_workflow.py` case that, for each of the three files, runs
   `fill_placeholders_for_validation` → `json.loads`, then for all **32** subsets of the
   five keys calls `bypass_optimisation_nodes` and asserts every `[node_id, idx]`
   reference still points at a node that exists, and that the guider and scheduler
   still reach the `UNETLoader`. This is the check that catches a bad rewire offline.
2. **Unit tests** — `tests/test_workflow.py` for the stripper (copy the
   `TestDropNodeOutputLinks` fixture shape at `tests/test_workflow.py:208`, including its
   "no-op leaves the graph byte-identical" idiom); `tests/test_app_routes.py::TestImage2VideoSettings`
   (`:784`) for forwarding + the unknown-key 400; `tests/test_generation_service.py` for
   an end-to-end graph assertion against `FakeServer`; `tests/js/utils.test.js` for
   `videoOptsPayload`.
3. `./scripts/test-all` and `npm run test:js`; `node --check static/js/{chat,commands,state,utils}.js`
   (curly-quote corruption — see CLAUDE.md).
4. **Manual, on the live box** — copy the three templates to `~/comfy-workflows/{image2video,text2video}/`
   on `$PROD_SERVER`, then: `/video-settings` shows five ticked boxes; ticking/unticking
   Turbo moves Steps between `4`/override-on and `20`/use-default; a run with all five on
   completes; a run with all five off produces a visibly better, slower video; the SSE
   progress log names the bypassed nodes.
5. **Test-render the default stack in the ComfyUI editor** — turbo + cache + sage + sol +
   spectrum together, per the warning above.
