# `/t2i-workflow-iterate`: one tick box per model

## Context

`/t2i-workflow-iterate <prompt>` runs one prompt against several t2i workflows as a
server-side batch run. When templates gained inline alternate models (a comma-separated
`UNETLoader.unet_name`, picked as `<workflow>@<model>` — see
`ADR/workflow-model-variants.md`), the iterate list still offered bare workflow names
only, so every workflow ran its default model and you could not compare models.

## Decision

Client-only. The batch-run endpoint already validates `workflow@model` step names
(`catalogue.resolve_workflow` checks the base against the allowlist; the model is
checked against the parsed graph by `workflow.select_model_variant`), so the server needed
no changes.

- **Variants are fetched eagerly, in parallel, when the list opens**:
  `Promise.all(workflows.map(wf => fetchWorkflowVariants('generation', wf)))`. The
  `/workflows` picker fetches lazily per click, but a tick list has to show every model up
  front. The cost is one template parse per t2i workflow each time the list opens. A
  failed lookup yields `[]`, and that workflow appears as a single bare row.
- **`expandWorkflowVariants(workflows, variantsByWf)`** (`utils.js`, unit-tested) turns
  the result into `{wire, workflow, model, isDefault}` entries. A workflow with fewer than
  two models is one entry with `model: null`. Otherwise there is one entry per model. The
  first is the template default and its wire name is **bare**, the same convention as
  `renderVariantButtons`, so a default pick stays byte-identical on the wire.
- **Rendering**: a single-model workflow is one row, as before. A multi-model workflow is
  a non-tickable header followed by indented model rows, with the first marked `(default)`.
- **Labels name the model**: each model checkbox has a `data-label` of `workflow@model`,
  used for the step's status label even when the wire name is bare. Without it, the
  default run of a workflow would carry an unqualified label next to its siblings' model
  labels.

## Not covered

- No "tick all models of this workflow" shortcut. Each model is ticked individually.
