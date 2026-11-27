---
name: adapt-workflow
description: Convert a ComfyUI workflow exported from the ComfyUI editor (API-format JSON, usually in ~/Downloads or /data/Downloads) into a comfy-chatbot workflow template — swapping hardcoded values for <PROMPT>, <LORA_n_NAME>/<LORA_n_STRENGTH>, <INPUT_IMAGE>, <DENOISE>, video and reference tokens, inserting a LoRA chain, removing nodes the app replaces, validating it against the app's own workflow.py code and installing it on the prod workflow mount. Use when the user says "adapt this workflow", "make this workflow work with the app", "add <PROMPT> and the other tags to this", "turn this into a template", "process this workflow json", or hands over a ComfyUI .json to use as a t2i/i2i/i2v/t2v/face-detail/upscale/inpaint/removal workflow.
---

# Adapt a ComfyUI workflow into an app template

The app fills **placeholder tokens** in a JSON template (`workflow.py`,
`generation_service._run_generation_core`) and does node surgery on it before
submitting it to ComfyUI. A raw export from the ComfyUI editor has hardcoded values
instead, so it has to be converted first. The token tables and per-kind rules are in
the **Workflow Template Parameters** section of `CLAUDE.md`: read the parts that cover
the kind you are converting before you start.

## 1. Inspect the export

- It must be **API format**: a top-level object of `"node_id": {class_type, inputs,
  _meta}`. A UI-format export (with `nodes`/`links` arrays) can't do the node surgery
  (LoRA, reference and last-frame stripping, optimisation bypass), because all of that
  runs before `convert_ui_to_api_format`. Ask for an API-format re-export.
- Work out the **kind** from what the graph does. That decides the folder under the
  workflow root (`config.WORKFLOW_KIND_DIRS`):

  | kind | folder | must have |
  |---|---|---|
  | text-to-image | `generation/<family>/` | `<PROMPT>` |
  | image-to-image | `image2image/` | `<PROMPT>`, `<INPUT_IMAGE>`, usually `<DENOISE>`, optional `<WIDTH>`/`<HEIGHT>` |
  | face detail | `facedetailer/` | `<INPUT_IMAGE>`, `<PROMPT>`, `<DENOISE>` |
  | upscale | `upscaler/` | `<INPUT_IMAGE>` |
  | inpaint / removal | `inpainting/`, `removal/` | `<INPUT_IMAGE>`, `<INPUT_MASK>` |
  | image-to-video | `image2video/` | `<PROMPT>`, `<INPUT_IMAGE>`, `<FRAMES>`, `<FPS>`, `<VIDEO_WIDTH>`, `<VIDEO_HEIGHT>` |
  | text-to-video | `text2video/` | as i2v, but **no** `<INPUT_IMAGE>` |

  Check this against the existing templates in that folder. Copying their conventions
  is what makes a template predictable.
- Notice anything **non-core** (custom-node classes such as `ResolutionSelector`) and
  anything that duplicates a job the app already does.

## 2. Convert

Write a small Python script in the scratchpad that loads the export, edits the dict
and writes the template. Don't hand-edit a large JSON file. Run it with `python3 -I`
because the export is a downloaded, untrusted file.

- **Prompt**: put `"<PROMPT>"` in the node holding the user text (a
  `PrimitiveStringMultiline`, or the `text` of the positive `CLIPTextEncode`). It is a
  string slot, so it stays quoted. If a prompt-enhancer switch exists, make sure the
  prompt feeds **both** branches. Leave the negative prompt and any system prompt as
  they are.
- **LoRAs**: insert a chain of `LoraLoaderModelOnly` nodes (5 slots for t2i, as
  `generation/krea-2/krea2_turbo_t2i.json` has) between the `UNETLoader` and **every**
  consumer of its MODEL output. Find every consumer: switch nodes (`on_false`/`on_true`),
  SLG, sage-attention patches and so on. Use titles `Load LoRA n`, names
  `"<LORA_n_NAME>"` and `strength_model: <LORA_n_STRENGTH>` **unquoted** (it's numeric).
  Emit a quoted sentinel from `json.dumps` and then string-replace it with the bare
  token. Unused slots are stripped at run time (`strip_lora_nodes`), so a full chain
  costs nothing. Use a `LoraLoader` (model + clip) only if the graph already
  conditions through one.
- **Resolution (images)**: `apply_resolution` overwrites `width`/`height` on any node
  that has both, but only when the user has set one. Give the latent node literal ints
  (e.g. 1024×1024) and **delete** any resolution helper feeding it, which would
  otherwise be dead weight and a custom-node dependency. Image-to-image uses
  `<WIDTH>`/`<HEIGHT>` instead (bare numbers).
- **Numeric tokens** (`<DENOISE>`, `<FRAMES>`, `<FPS>`, `<DURATION>`, `<VIDEO_*>`,
  `<WIDTH>`/`<HEIGHT>`, `<LAST_FRAME_STRENGTH>`, LoRA strengths) are **unquoted**.
  String tokens (`<PROMPT>`, `<INPUT_*>`, `<REFERENCE_*>`, LoRA names) are quoted.
- **Leave alone**: seeds (`randomize_seeds`/`apply_seed` handle `seed`/`noise_seed`),
  `steps` (`apply_steps`), sampler, CFG, VAE and CLIP loaders.
- **Alternate models**: several interchangeable diffusion models become one
  comma-separated `unet_name` (see "Alternate models" in `CLAUDE.md`). Don't make one
  file per model.
- **Video optimisations**: speed nodes that should be switchable get an
  `[opt:<key>]` title prefix (see "Video optimisation toggles").
- Give `SaveImage`/`SaveVideo` a sensible `filename_prefix`. Keep the node IDs from the
  export, so the template still diffs against a re-export.

## 3. Validate against the app's code

```bash
cd ~/Code/comfy-chatbot
python3 .claude/skills/adapt-workflow/scripts/validate_template.py <template.json> [--image]
```

The script runs the template through `workflow.py`. It parses with validation dummies,
fills `<PROMPT>` with a quoted string (to check the escaping) and one LoRA slot, strips
the unused LoRA nodes, then reports any leftover placeholders, dangling links, LoRA
rewiring, the latent size after `apply_resolution`, and the seeds it found. Pass
`--image` for kinds that take `<INPUT_IMAGE>`. Fix everything it reports before you
install. Then say plainly that the template has **not** been test-rendered in ComfyUI.

## 4. Install

The live workflows are on the prod server, `$PROD_SERVER` (moria), under
`~/comfy-workflows/<kind-folder>/`. That folder is bind-mounted into the container at
`/app/workflows`.

**Ask the user's permission before accessing the prod server at all**, whether to
read the existing templates or to copy a new one in. If you can't get it, or a
permission check blocks the copy, leave the template in the scratchpad and give the
user the exact command to run with `!`:

```
! scp <scratchpad>/template.json moria:comfy-workflows/<kind-folder>/
```

Never overwrite an existing template without showing what would change. The app
reads the folder whenever a picker opens, so no redeploy is needed. Tell the user
which `/…-workflow` picker to select it in.
