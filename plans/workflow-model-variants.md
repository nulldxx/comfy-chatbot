# Alternate models in a workflow (comma-separated `UNETLoader` names)

## Context

Several workflow templates in `~/comfy-workflows/` exist in near-duplicate pairs that
differ in **nothing but the diffusion model file** — an int8 build and an fp16 build of
the same model. That is the same DRY failure the H3 optimisation toggles fixed
(`ADR/`-documented: eight templates collapsed to three), and it has the same cost: a
graph edit has to be repeated in every copy, and the picker fills up with near-identical
entries.

The fix mirrors that one. A template declares its alternates **inline**, as a
comma-separated `unet_name` on a `UNETLoader` node:

```json
"105:6": {
  "_meta": { "title": "Load Diffusion Model" },
  "class_type": "UNETLoader",
  "inputs": {
    "unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors, minimax_h3_fl2va_pruned_fp16.safetensors",
    "weight_dtype": "default"
  }
}
```

and the existing `/workflows` drill-down (type → workflow) gains a **third level**
(type → workflow → model). The comma list is collapsed to a single name during the
usual node surgery, so ComfyUI never sees it. One file per graph, one pick per run.

Decisions taken with the user:

- **Wire format: a `@<model>` suffix on the workflow name.** The picker writes
  `minimax-h3-i2v@minimax_h3_fl2va_pruned_fp16` into the *same* state field it already
  uses, so the choice rides every existing payload, `saveSession`/`restoreSession`, the
  `/settings-save` stack, `newChat`, macros and server-side sequence runs **with no new
  plumbing** — exactly the property that made the flat `optTurbo`/`optCache` booleans
  free. Cost: the suffix is visible in the header badge and `/jobs` summaries.
- **Several multi-valued loaders are index-paired.** Wan 2.2 carries a high-noise and a
  low-noise `UNETLoader`; if both declare alternates they must declare the *same number*,
  and picking variant *n* takes the n-th entry from each. Mismatched counts are an error,
  not a guess.
- **Both picker surfaces** get the extra level: the `/workflows` table and the shared
  `renderWorkflowPicker` helper behind the eight `/<x>-workflow` commands — so a per-type
  command can't silently drop a variant the table set.

---

## Server

### `workflow.py` — graph-level primitives (new)

Follows the `optimisation_nodes` / `bypass_optimisation_nodes` pair (`workflow.py:206`)
in shape and in the "raise rather than submit a broken graph" convention.

```python
# Loader classes whose model slot may declare comma-separated alternates.
# A dict so a later CheckpointLoaderSimple/VAELoader is a one-line addition.
MODEL_VARIANT_INPUTS = {"UNETLoader": "unet_name"}
WORKFLOW_VARIANT_SEP = "@"
```

- `model_variant_nodes(workflow)` → `[(node_id, input_key, [alt, ...]), ...]` for nodes in
  `MODEL_VARIANT_INPUTS` whose value is a string containing a comma; entries `.strip()`ed,
  blanks dropped, sorted by node id for determinism. Nodes with a single value are not
  returned.
- `model_variant_labels(workflow)` → `[] ` when no node declares alternates, else the
  label list: the **stem** (basename minus `.safetensors`) of each of the *first* node's
  entries. Raises `ValueError` if two multi-valued nodes disagree on count (names the
  node ids and counts).
- `select_model_variant(workflow, variant=None)` → collapses **every** multi-valued node
  to its chosen entry, so a comma list can never reach ComfyUI. `variant=None` → index 0.
  A non-`None` variant that matches no label raises `ValueError` listing what is
  available; a variant supplied for a template that declares none likewise raises.
  Returns the chosen `(label, index)` or `None` for a template with no alternates.
- `split_workflow_variant(name)` → `(base, variant|None)`, an `rsplit` on the last `@`.

**Call site:** `generation_service._run_generation_core`, immediately **after** the
UI→API conversion and before `apply_resolution` (`generation_service.py:559-565`).
Deliberately *after* the conversion rather than beside the other surgery: it only
rewrites an input string (no rewiring, so nothing depends on ordering), and running it
post-conversion makes it work for UI-format templates too. Emits the usual
`send("progress", message=f"Model: {label}")` when a variant is chosen.

### `catalogue.py` — file-level listing (new)

- `resolve_workflow_path(base_dir, name)` — extract the `.json`-suffixing +
  `is_relative_to` traversal guard currently inlined in `_run_generation_core`
  (`generation_service.py:332-338`) so the new endpoint and the generation path share
  one guard. `_run_generation_core` is changed to call it (`TestRunGenerationTraversalGuard`
  in `tests/test_workflow_dirs.py` must keep passing unchanged).
- `list_workflow_variants(base_dir, name)` → `[]` or the label list. Reads the template,
  reuses the existing **`workflow.fill_placeholders_for_validation`** (a raw template is
  not valid JSON — it still holds `<PROMPT>` etc.), `json.loads`, then
  `model_variant_labels`. Any failure (missing file, unparseable, UI-format graph with no
  `class_type` keys) returns `[]` — the picker degrades to today's behaviour rather than
  erroring. No caching: the files are small and this is one fetch per picker click.

### `resolve_workflow` (`catalogue.py:151`)

Split the `@` suffix before the allowlist check and re-attach it to the returned name:
if the whole string is in `available` treat it as a literal filename (a workflow whose
name genuinely contains `@`), otherwise validate the base and return `base@variant`
unchanged. The **variant itself is not validated here** — that needs the parsed graph,
and is done once in `select_model_variant`.

### `_run_generation_core` (`generation_service.py:301`)

Split the name once at the top: the base drives `resolve_workflow_path`, the variant is
handed to `select_model_variant` at step 10.5. This is the single choke point that also
covers `/api/generate`, which does **not** call `resolve_workflow`, and server-side
sequence runs, whose workflow comes from `_parse_gen_settings` (`app.py:1635`). No new
kwarg, no signature change, no endpoint change.

### `config.py` + `app.py` — the variants endpoint

- `config.WORKFLOW_KIND_DIRS` — a `{kind: dir}` registry for the eight existing
  directories (`generation`, `facedetailer`, `upscaler`, `image2image`, `inpainting`,
  `image2video`, `text2video`, `removal`). The eight listing endpoints
  (`app.py:451-496`) are left alone.
- `GET /api/workflow-variants/<kind>/<path:name>` (`@login_required`) → `{"variants": [...]}`;
  unknown kind → 404.

---

## Client

### `static/js/utils.js` (pure, unit-testable — the established split, since `commands.js` has no test environment)

```js
export const WORKFLOW_VARIANT_SEP = '@';
export function splitWorkflowVariant(name)          // -> { name, variant|null }
export function joinWorkflowVariant(name, variant)  // variant null/first -> bare name
export function workflowLabelHtml(name)             // "wf" + dimmed " @ variant"
```

`joinWorkflowVariant` returns the **bare name** for the default (first) alternate, so an
unchanged pick keeps the wire format byte-identical to today's.

### `renderWorkflowPicker` (`commands.js:12`) — one shared helper, eight commands

Add an optional `kind`. On clicking a workflow, fetch
`/api/workflow-variants/<kind>/<wf>`; with ≤1 variant select immediately as now, with >1
replace the list with the model buttons (first one captioned `(default)`), then
`onSelect(joinWorkflowVariant(wf, v))`. `isCur` compares via `splitWorkflowVariant`. A
failed fetch falls back to selecting the bare name — never block a pick. `kind` is added
to the eight call sites (`/t2i-workflow` 2412, `/i2i-workflow` 1673, `/i2v-workflow` 1784,
`/t2v-workflow` 1827, `/inpaint-workflow` 1899, `/removal-workflow` 1953,
`/upscale-workflow` 1981, `/face-detail-workflow` 2438).

### `renderWorkflowsTable` (`commands.js:1170`) — the third level

- `WORKFLOW_TYPES` (1139) gains a `kind` per entry.
- `renderChoices` gains a sibling `renderVariants(row, wf, variants)` that swaps the
  workflow list for the model list plus a `‹ back` button, mirroring the existing
  `collapse`/caret idiom. Variants are cached on the row alongside `row.workflows`.
- `valueHtml` renders through `workflowLabelHtml` so the row shows
  `minimax-h3-i2v` + a dimmed `@ …fp16`.

**Out of scope:** `/t2i-workflow-iterate` (`commands.js:2041`) keeps passing bare names,
i.e. each iterated workflow runs its default model. Noted, not changed.

---

## Tests

- **`tests/test_workflow.py`** — new `TestModelVariants` / `TestSelectModelVariant`,
  cloning the `_workflow()` fixture at line 257 (it already has a `UNETLoader` at the
  head): single multi-valued node; two index-paired nodes; mismatched counts raise;
  unknown variant raises and names the alternatives; a variant on a template with none
  raises; single-valued and non-loader nodes untouched; whitespace around commas;
  `variant=None` still collapses to entry 0; `split_workflow_variant` edge cases.
- **`tests/test_catalogue.py`** — `list_workflow_variants` over a tmp dir: a template
  with commas, one without, a UI-format file, a missing file, a `../` escape; plus
  `resolve_workflow` round-tripping `name@variant` and preferring a literal `@` name
  present in `available`.
- **`tests/test_generation_service.py`** — clone the `OPT_TEMPLATE` + `_run_opts`
  harness (line 720/734, its `MagicMock` server captures the submitted graph): assert the
  submitted `unet_name` is the picked alternate for `workflow_name="tmpl@b"`, is entry 0
  with no suffix, and that an unknown suffix fails the job with a readable error.
- **`tests/test_app_routes.py`** — `/api/workflow-variants/<kind>/<name>` 200 / unknown
  kind 404 / unknown workflow → `{"variants": []}`, using the existing
  `patch.object(app_module, "COMFY_WORKFLOW_DIR", tmpdir)` fixture pattern (line 249).
- **`tests/js/utils.test.js`** — split/join round-trip, names containing `@`, default
  variant → bare name.

## Verification

1. `./scripts/test-all` (Python imports + unit tests + Docker container tests) and
   `npm run test:js` — the JS suite is **not** part of `test-all`.
2. `node --check static/js/commands.js static/js/utils.js` after every JS edit — the
   curly-quote corruption pitfall in `CLAUDE.md`.
3. `docker-compose up --build -d`, then in the UI: `/workflows` → *Image → video* →
   a template with a comma list → confirm the third level lists both models, that
   picking the second shows `@…` on the row, and that `/i2v-workflow` shows the same two
   models and ticks the one already chosen.
4. Generate with each variant and check `/api/last-sent-workflow` (or the `_last_sent`
   record) shows the collapsed single `unet_name` — no comma reaches ComfyUI.
5. Regression: a template with **no** comma list must behave exactly as before (bare
   name on the wire, no extra level, one fetch returning `[]`).
6. Reload the page mid-session and confirm the variant survives `restoreSession`; run
   `/settings-save` → change → `/settings-restore` and confirm it round-trips.

## Docs

- New `CLAUDE.md` section, **"Alternate models in a workflow"**, after the video
  optimisation toggles: the comma convention, `UNETLoader`-only and why, index-paired
  multi-loader semantics, the `@` wire suffix and its one ambiguity (a filename
  containing `@`), the API-format requirement for *listing* (a UI-format template shows
  no alternates even though generation would collapse them), and that the collapse
  always runs so a comma list never reaches ComfyUI.
- Per repo convention: commit this plan under `plans/` before implementing, and rewrite
  it into `ADR/` afterwards.
