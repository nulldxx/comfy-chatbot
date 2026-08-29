# ADR: Alternate models in a workflow (comma-separated `UNETLoader` names)

**Status:** implemented.

## Context

Several templates in `~/comfy-workflows/` existed in near-duplicate pairs differing in
**nothing but the diffusion model file** — an int8 build and an fp16 build of the same
graph. Same DRY failure the H3 optimisation toggles fixed (`ADR/h3-video-optimisations`:
eight templates collapsed to three), same cost: every graph edit repeated per copy, and
a picker full of near-identical entries.

## Decision

A template declares its alternates **inline**, as a comma-separated model name on a
`UNETLoader`, and the `/workflows` drill-down gains a third level (type → workflow →
model):

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

### The pick rides the workflow name

The wire format is `<workflow>@<model>` (`WORKFLOW_VARIANT_SEP`), where `<model>` is the
filename without its extension. This was the decisive choice: the picker writes it into
the *same* `state.current*Workflow` field it always used, so the model rides every
generation payload, `saveSession`/`restoreSession`, the `/settings-save` stack,
`newChat`, macros and the server-side `/api/sequence-run` **with no new field anywhere** —
the same property that made the flat `optTurbo`/`optCache` booleans free. The rejected
alternative (a `state.workflowVariants` map plus a `model_variant` payload field) would
have needed threading through ~12 JS call sites, 7 endpoints, both restore paths and
`newChat`. Accepted cost: the suffix is visible in the header badge and `/jobs` summaries.

Splitting is on the **last** `@` (`split_workflow_variant` in `workflow.py`,
`splitWorkflowVariant` in `utils.js`). A workflow whose own filename contains one still
resolves, because the suffixed reading is only preferred when the base file actually
exists (`resolve_workflow_path`) or when the whole string misses the allowlist
(`resolve_workflow`).

### Two server choke points, no signature changes

- **`catalogue.resolve_workflow`** validates only the workflow half against the
  allowlist and returns the name with its suffix intact. The model itself is *not*
  validated here — that needs the parsed graph.
- **`generation_service._run_generation_core`** splits once at the top via the new
  **`catalogue.resolve_workflow_path`** (the `.json`-suffixing + `is_relative_to`
  traversal guard, extracted from the inline version so the endpoint and the generation
  path share one guard), then calls `workflow.select_model_variant` on the parsed graph.
  This single point also covers `/api/generate`, which never calls `resolve_workflow`,
  and sequence runs, whose workflow comes from `_parse_gen_settings`.

`select_model_variant` **always runs**, defaulting to the first alternate, so a comma
list can never reach ComfyUI. It sits *after* the UI→API conversion, unlike the rest of
the node surgery, because it only rewrites an input string — nothing depends on the
ordering, and running it there covers UI-format templates too. An `@model` the template
doesn't offer **fails the job** naming what it does offer, rather than quietly rendering
a different model.

### Index-paired multi-loader semantics

Wan 2.2 carries a high-noise and a low-noise `UNETLoader`. If several declare alternates
they must declare the **same number**, and variant *n* takes the n-th entry from each —
one pick, a matched pair of builds. A count mismatch raises rather than pairing the wrong
files. Labels come from the first node, sorted by node id so they are stable.

Matching is keyed on `class_type` (`MODEL_VARIANT_INPUTS = {"UNETLoader": "unet_name"}`),
deliberately unlike the `[opt:…]` title markers: there is no ambiguity to resolve here —
only a model loader has a model slot, and a node holding a single name is not a choice.

### Listing is lazy, and needs API format

`GET /api/workflow-variants/<kind>/<name>` → `{"variants": [...]}`, backed by
`catalogue.list_workflow_variants`, which reuses the existing
`fill_placeholders_for_validation()` (a raw template isn't valid JSON — it still holds
`<PROMPT>`) before parsing. `kind` indexes the new `config.WORKFLOW_KIND_DIRS`; unknown
→ 404. Every failure — missing file, unparseable template, UI-format export with no
`class_type`, mismatched lists — answers `[]`, so the picker simply shows no extra level.

Fetched per workflow *clicked*, not folded into the eight listing endpoints, which would
mean parsing every template on every picker open. Cached per row in the `/workflows`
table.

### Both picker surfaces

The third level lives in `renderWorkflowsTable` **and** in the shared
`renderWorkflowPicker` (the eight `/<x>-workflow` commands), sharing two new module-level
helpers, `fetchWorkflowVariants` and `renderVariantButtons` — so a per-type command can't
silently drop a model the table set. The first alternate is captioned `(default)` and
stores the **bare** name, keeping the wire format byte-identical for anyone who never
touches it. `workflowLabelHtml` renders a suffixed name as the workflow plus a dimmed
model.

## Consequences

- One template file per graph; the model is a per-run choice that persists with the chat
  session and the settings snapshot stack, like every other workflow selection.
- A **UI-format** template shows no alternates in the picker even though generation would
  collapse them — listing needs `class_type`. Same API-format requirement the rest of the
  node surgery has.
- `/t2i-workflow-iterate` passes bare names, so each iterated workflow runs its default
  model. Not changed.
- A model filename containing a comma can't be used.
- A stale pick (template edited to drop a model) fails the job with a readable error
  rather than rendering the wrong model; fixed by re-picking in `/workflows`.

## Files

`workflow.py` (`MODEL_VARIANT_INPUTS`, `model_variant_nodes`, `model_variant_labels`,
`select_model_variant`, `split_workflow_variant`), `catalogue.py`
(`resolve_workflow_path`, `list_workflow_variants`, `resolve_workflow`), `config.py`
(`WORKFLOW_KIND_DIRS`), `app.py` (`/api/workflow-variants/<kind>/<name>`),
`generation_service.py` (`_run_generation_core`), `static/js/utils.js`,
`static/js/commands.js`. Tests in `tests/test_workflow.py`, `tests/test_catalogue.py`,
`tests/test_generation_service.py`, `tests/test_app_routes.py`, `tests/js/utils.test.js`.
