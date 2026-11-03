# `/t2i-workflow-iterate`: tick models, not just workflows

## Context
`/t2i-workflow-iterate <prompt>` shows a tick list of t2i workflows and runs the prompt
against each ticked one via `runBatchJob`. Templates can now declare alternate models
inline (comma-separated `UNETLoader.unet_name`, picked as `<workflow>@<model>`), but the
iterate list only offers bare names, so every workflow runs its default model — the gap
recorded as "Not covered" in `ADR/workflow-model-variants.md` and CLAUDE.md. Goal: one
tick box per (workflow, model) so a prompt can be compared across models too.

The server already accepts `workflow@model` in a batch step (`catalogue.resolve_workflow`
validates the base against the allowlist, `select_model_variant` validates the model), so
this is **client-only**.

## Changes

1. **`plans/t2i-workflow-iterate-models.md`** — save this plan and commit before coding
   (per CLAUDE.md).

2. **`static/js/utils.js`** — new pure helper next to `joinWorkflowVariant`:
   `expandWorkflowVariants(workflows, variantsByWf)` → `[{ wire, workflow, model, isDefault }]`.
   A workflow with < 2 variants yields one entry (`wire = wf`, `model = null`); one with
   alternates yields one per model, the first being `wire = wf` (bare, matching
   `renderVariantButtons`' convention) with `isDefault: true`, the rest
   `joinWorkflowVariant(wf, v)`. Order preserved.

3. **`static/js/commands.js`** (`/t2i-workflow-iterate` block, ~line 2450):
   - After `fetch('/api/workflows')`, show "Loading models…" and fetch all variants in
     parallel: `Promise.all(workflows.map(wf => fetchWorkflowVariants('generation', wf)))`
     (existing helper; failures already yield `[]`, so a bad template just shows bare).
   - Build the list from `expandWorkflowVariants`. Single-model workflows render as today.
     A multi-model workflow renders a non-tickable header row with its name, then an
     indented checkbox per model (`escapeHtml(model)` + dimmed `(default)` on the first).
     Checkbox `value` = the wire name.
   - Step labels use the wire name: `` ` — ${wire} (${i+1}/${n})` ``; the "Generating N
     workflow(s)" text becomes "N run(s)". Rest (validation, disabling, `runBatchJob`)
     unchanged.

4. **Docs**: remove the `/t2i-workflow-iterate` "Not covered" bullet from CLAUDE.md
   ("Alternate models in a workflow") and `ADR/workflow-model-variants.md`, replacing with
   a line saying the iterate list expands each workflow into one tick box per model
   (variants fetched in parallel on open). Update the help text in `commands.js` (~2683)
   to "tick workflows/models". Write `ADR/t2i-workflow-iterate-models.md` after.

## Verification
- `node --check static/js/commands.js static/js/utils.js` (curly-quote pitfall).
- Add Jest cases in `tests/js/utils.test.js` for `expandWorkflowVariants`: no variants,
  single variant (treated as none), multi-variant (first bare + default flag, rest
  suffixed), mixed ordering. Run `npm run test:js`.
- `./scripts/test-all` for the Python/Docker suite (no backend change expected).
- Manual: `/t2i-workflow-iterate a cat` against a template with a comma `unet_name` —
  confirm one row per model, tick the non-default one, and the generated image's
  `/jobs`/label shows `wf@model` and the right model loaded in ComfyUI.
