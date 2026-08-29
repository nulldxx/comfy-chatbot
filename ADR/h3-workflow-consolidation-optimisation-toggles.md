# H3 workflow consolidation behind `/video-settings` optimisation toggles

## Context

`/data/Downloads/h3` held **eight** MiniMax H3 templates that differed only in which
speed-for-quality optimisations were baked into the model chain — 4-step turbo LoRA, H3
FirstBlockCache, Sage attention, Sol attention, H3 Spectrum — plus the sampler step count
that went with them (4, 26 or 32). Every one of those optimisations is a `MODEL → MODEL`
passthrough node sitting in a single chain between the `UNETLoader` and the
guider/scheduler, so the matrix was combinatorial for no structural reason: adding a sixth
optimisation meant authoring another generation of files.

Collapsed to **three** templates — one per generation kind — with the optimisations
toggled per run from `/video-settings`. Adding a seventh now means one node and one
checkbox.

## Decisions

- **All five default to on**, as a deliberately fast, low-quality preview mode.
  ⚠ Turbo + cache + Spectrum is an **untested stack**: Spectrum previously appeared only
  in the 32-step HQ t2v file, where it *replaced* FirstBlockCache and fed the guider
  directly, and its forecast parameters (warmup 1, window 2, max_history 8, tail 1) have
  little to work with across 4 steps. Still to be test-rendered in the ComfyUI editor.
- **Steps stay a `/video-settings` concern.** Templates bake `steps: 20`; ticking Turbo
  switches the override on and sets it to 4, unticking it restores *Use workflow default*.
- **Three files, not one.** R2V uses a different UNET (`minimax_h3_ref2va_…`) with the
  15-slot `MiniMaxH3ReferenceToVideo` node, and I2V adds a
  `LoadImage → resize → first_frame` sub-chain to T2V's graph. These are different graphs,
  not optimisation variants, so merging them would have meant node stripping at run time.
  R2V and T2V both live in `text2video/` (neither takes `<INPUT_IMAGE>`); the picker is
  `/t2v-workflow`.

## What was built

### The templates

`image2video/minimax-h3-i2v.json`, `text2video/minimax-h3-t2v.json`,
`text2video/minimax-h3-r2v.json`, each derived from its `-turbo-4step` predecessor, which
already carried four of the five in a consistent order. The eight originals moved to
`/data/Downloads/h3/.superseded/`. The derivation ran as a script rather than by hand, via
a small loader that substitutes each `<TOKEN>` with a unique negative integer literal —
valid JSON whether the token sits as a whole string value, a bare number, or a substring
inside a math expression, and reversible with one text replace.

```
UNETLoader → [opt:turbo] LoRA → [opt:accel8*] LoRA → [opt:sage] → [opt:sol] → [opt:cache] → [opt:spectrum] → BasicGuider.model
                                                                                                          → BasicScheduler.model
```

(The `[opt:accel8*]` node was added later — see the amendment at the foot of this file.)

Spectrum sits outermost so it wraps the rest. Beyond marking and adding it, the pass also
closed real gaps in placeholder coverage:

- **I2V** gained `<DURATION>` and `<FPS>`, which it did not have (duration was hardcoded
  `5`, fps `24` in both the length expression and `CreateVideo`).
- **R2V** gained `<FPS>`, and `<VIDEO_WIDTH>`/`<VIDEO_HEIGHT>` — its fixed
  `ResolutionSelector` was replaced with the `PrimitiveInt` + snap-to-32
  `ComfyMathExpression` pair T2V already used, so `/video-settings` now drives R2V's size.
- The dangling `MiniMaxH3MemoryEfficientSageAttentionPatch` node in both turbo files
  (no inputs, no consumers) was dropped as dead weight, not treated as a sixth toggle.

All three now expose the same video slots, and no new placeholder token was introduced —
`fill_placeholders_for_validation` needed no change.

### Marking and bypass

A node is an optimisation iff its `_meta.title` starts with `[opt:<key>]`
(`OPT_TITLE_RE`, `optimisation_nodes`, `workflow.py`). Deliberately **not** `class_type`:
the i2v graph holds two `LoraLoaderModelOnly` nodes — the turbo LoRA and the
`<LORA_1_NAME>` user slot — and only the first may be bypassed. Titles also survive a
ComfyUI re-export, so editing a template in the editor does not lose the marking.

`bypass_optimisation_nodes(workflow, disabled)` deletes each marked node and rewires its
consumers to whatever fed its `model` input, reusing the `_rewire_references` passthrough
`strip_lora_nodes` uses. Nodes go **one at a time**, which makes chained removals correct
with no special case: dropping sage first repoints sol's `model` at sage's upstream. A
marked node with no `model` input raises rather than leaving a dangling reference — the
same "fail loudly over submitting a wrong graph" rule `_apply_track_drop` follows.

Called from `_run_generation_core` with the other node surgery, after the track drops and
before the UI→API conversion; like all of it, this requires API-format templates.

### Wire and state

`config.py` holds `VIDEO_OPTIMIZATIONS` as the canonical key list. The client sends
`video_opts` as `{key: bool}` for **all five** keys — an absent `video_opts` disables
nothing, so omitting the off ones would invert their meaning. `_parse_video_opts`
(`app.py`) returns the set that is off, forwarded as `disabled_optimizations` from both
video routes; unknown keys are a 400.

Client state is **five flat booleans** on `currentVideoSettings` (`optTurbo`, `optCache`,
`optSage`, `optSol`, `optSpectrum`), read with the `!== false` idiom so absent means on.
Flat rather than a nested `opts` object because both restore paths shallow-merge
(`{ ...DEFAULT_VIDEO_SETTINGS, ...s.videoSettings }`) and Apply does `{ ...work }`: a
nested object would be replaced wholesale by an old snapshot and would share a reference
between the panel and state. Flat keys ride the existing spread, so `saveSession`,
`restoreSession`, the `/settings-save` stack and `newChat` needed **no** changes — exactly
as when `audio` was added.

### Panel

The `/video-settings` boolean rows now go through one `mkCheckbox` factory that registers
every box it makes, so Reset re-syncs them all — previously each checkbox was hand-synced
there, and a forgotten one showed a stale tick. A `refreshSteps()` extraction likewise
gives the steps slider, text input and *Use workflow default* box a single sync point,
shared by manual edits, the Turbo coupling and Reset.

## Deviations from the plan

- **`VIDEO_OPTIMIZATIONS` lives in `utils.js`, not `state.js`.** `state.js` imports
  `DEFAULT_VIDEO_SETTINGS` from `utils.js`, so putting it in `state.js` would have made
  the cycle `utils → state → utils`. `utils.js` is also where the settings defaults and
  the `videoOptsPayload` helper already sit.
- **The all-32-combinations test is synthetic.** The real templates live outside the repo
  (`/data/Downloads/h3`, deployed to `~/comfy-workflows/`), so a committed test cannot
  read them. `TestBypassOptimisationNodes.test_every_subset_leaves_the_graph_intact`
  runs the invariant over a synthetic five-node chain; the same check was run once against
  all three real files (32/32 intact, guider and scheduler still tracing to the
  `UNETLoader` in every combination) as a build-time verification.

## Known gap

On a fresh session where `/video-settings` has never been opened, `currentVideoSteps` is
`null` while Turbo is on, so the first run loads the turbo LoRA at the template's baked-in
20 steps — slower than intended, not broken. Opening the panel once (it pre-fills the
override to 4 in that case) and pressing Apply fixes it for the session.

Defaulting `currentVideoSteps` to 4 instead was rejected: it is global across all video
workflows, so it would silently drop the Wan 2.2 and LTX 2.3 templates — which have no
`[opt:turbo]` node to untick — to 4 steps. For the same reason the Turbo↔steps coupling
lives in the panel and not at send time.

## Verification

- `tests/test_workflow.py::TestBypassOptimisationNodes` — marking, single and chained
  removals, the unmarked same-class LoRA surviving, no-op cases, the raise, and the
  all-subsets graph-integrity invariant.
- `tests/test_app_routes.py` — `video_opts` forwarding for i2v and t2v, absent-means-none,
  and the 400 paths for an unknown key and a non-object.
- `tests/test_generation_service.py` — end-to-end through `_run_generation_core`: the
  bypassed node is gone and the graph rewired.
- `tests/js/utils.test.js` — `videoOptsPayload` sends every key, defaults all on, and
  reads a pre-upgrade settings object as all on.
- Full suite: 583 passed / 5 skipped; 155 JS tests.

**Still outstanding:** test-render the default all-on stack in the ComfyUI editor, and
deploy the three templates to `~/comfy-workflows/{image2video,text2video}/` on
`$PROD_SERVER`.

---

# Amendment: the 8-step accelerator LoRAs

## Context

A second distillation LoRA arrived in two flavours — `minimax-h3-fl2va-acc-8step`
(matching the `fl2va` UNET the i2v and t2v graphs use) and `minimax-h3-ref2va-acc-8step`
(matching r2v's `ref2va`). Eight steps buys noticeably better quality than four, so it
becomes the default accelerator; the 4-step turbo LoRA stays available.

This is exactly the "adding a seventh now means one node and one checkbox" case the
consolidation was built for, and it played out that way: `workflow.py`,
`generation_service.py` and `app.py` needed **no** change at all, `config.py` one line.
The one genuinely new mechanic is that accelerators must exclude each other.

## Decisions

- **Mutually exclusive by step count, not by key.** Chaining a 4-step and an 8-step
  distillation LoRA gives mush. Ticking an accelerator unticks every accelerator of a
  *different* count and moves the steps override to its own. The two 8-step variants
  share a count and so coexist — they are never both present in one template (fl2va in
  i2v/t2v, ref2va in r2v), so "both on" means "whichever 8-step LoRA this workflow has".
- **`steps` on the descriptor is what makes something an accelerator.** It replaced the
  hardcoded `stateKey === 'optTurbo'` test in the panel, so the exclusion group, the
  step-count coupling and the label hint all fall out of one field. `TURBO_STEPS` survives
  only as turbo's value.
- **Both 8-step rows are always shown**, captioned by workflow kind. The panel has no idea
  which workflow is active, and a key absent from a template bypasses nothing — so ticking
  the irrelevant one is a no-op, exactly as the original five are on a non-H3 template.
- **Default is 8-step on, turbo off.**

## The pre-upgrade session trap

Flags are read as "absent means on". A session saved before this change carries an
explicit `optTurbo: true` and *no* 8-step keys — which reads as all three accelerators on,
i.e. precisely the stacked graph to avoid. Shallow-merging the new defaults cannot fix it:
the old explicit `true` wins.

`activeAccelerator(vs)` (`utils.js`) resolves it in one rule — **the on accelerator with
the lowest step count wins** — so such a session resolves back to the turbo LoRA it
actually chose and renders exactly as it did before. Three call sites agree on it:
`videoOptsPayload` forces the losing group to `false` on the wire, the `/video-settings`
panel collapses its `work` copy the same way on open so the ticks match what will be sent,
and the `/settings` summary reads through the payload rather than the raw flags.

## What was built

- **Templates** (`~/Code/comfy-workflows`): one `LoraLoaderModelOnly` appended to each of
  the three H3 files, directly after `[opt:turbo]` in the chain, at `strength_model: 1.0`
  from the same `h3\` LoRA subfolder — `105:129` in i2v and t2v, `170` in r2v — with the
  node that used to consume turbo repointed at it. Edited as text: the templates hold
  unquoted placeholders and are not parseable JSON until `fill_placeholders_for_validation`
  runs over them.
- **`config.py`**: two keys added to `VIDEO_OPTIMIZATIONS`, and its stale "mirrored in
  state.js" comment corrected to `utils.js`.
- **`utils.js`**: two descriptors, the `steps` field, `activeAccelerator`, the exclusion
  rule inside `videoOptsPayload`, and the new defaults.
- **`commands.js`**: `collapseAccels()`/`accelSteps()` at panel open, a generic
  accelerator handler replacing the turbo special case in the checkbox loop, Reset and the
  Apply summary de-hardcoded off `TURBO_STEPS` and the literal `5`.

## Verification

- `tests/test_workflow.py::TestBypassOptimisationNodes` — the subset invariant now runs
  over all 128 combinations of seven keys, and its chain is asserted equal to
  `config.VIDEO_OPTIMIZATIONS` so the two cannot drift again.
  `test_key_absent_from_template_is_a_no_op` already covered the fl2va-on-r2v case.
- `tests/test_app_routes.py` — the i2v payload sends all seven keys.
- `tests/js/utils.test.js` — the new defaults, the exclusion on the wire, and
  `activeAccelerator` including the pre-upgrade case.
- Against the **real** templates, as before: all three parse after placeholder fill, carry
  exactly the expected `[opt:]` keys, and over all 64 subsets of their six keys leave no
  dangling reference with `BasicGuider` and `BasicScheduler` still tracing to the
  `UNETLoader`. Scripted, not committed — the templates live in another repo.
- Full suite: 660 passed / 5 skipped; 181 JS tests.

## Known gap carried forward

The original `currentVideoSteps === null` gap stands, now at 8 steps rather than 4: on a
fresh session where `/video-settings` was never opened, the first run uses the template's
baked-in 20 steps. Unchanged reasoning — the override is global across all video
workflows, which is why the coupling lives in the panel.

**Still outstanding:** test-render the new default stack (8-step accel + cache + sage +
sol + spectrum) in the ComfyUI editor, and deploy the three templates to
`~/comfy-workflows/{image2video,text2video}/` on `$PROD_SERVER`.
