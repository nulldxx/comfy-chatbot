# /t2v — text-to-video mode

## Context

Today every plain prompt typed into the chat goes to the text-to-image workflow
(`/api/generate` → `COMFY_GENERATION_DIR`). Video generation is only reachable *from an
existing image* — the 🎬 button, `/i2v`, or `/i2v <N>` — because `/api/image2video`
requires an `image` and the i2v templates carry an `<INPUT_IMAGE>` `LoadImage` node.

Newer video models (MiniMax H3, and the LTX 2.3 face-ID "ref_t2v" graph already in the
repo) are multi-modal and generate perfectly well from a prompt alone. There is currently
no way to reach them without first making a still image to throw away.

**Outcome:** a `/t2v` toggle. While it is on, anything typed into the chat is generated as
a video by the selected text-to-video workflow instead of an image by the t2i workflow.
Typing `/t2v` again turns it off.

**Approach chosen:** a dedicated `text2video/` workflow directory with its own
`/t2v-workflow` picker, mirroring the seven workflow-type dirs that already exist
(`generation/`, `image2video/`, `facedetailer/`, `upscaler/`, `image2image/`,
`inpainting/`, `removal/`). No graph surgery: a t2v template simply has no
`<INPUT_IMAGE>`, so nothing needs stripping and no `strip_*` helper is required.

---

## Backend

### 1. `config.py` — new dir + default (after the image2video block, ~line 94)

```python
# Text2video workflows live in a subdir of the main workflow folder. Unlike
# image2video they take NO <INPUT_IMAGE> — the video comes from <PROMPT> alone
# (plus the usual <DURATION>/<FRAMES>/<FPS>/<VIDEO_WIDTH>/<VIDEO_HEIGHT> slots).
COMFY_TEXT2VIDEO_DIR = COMFY_WORKFLOW_DIR / 'text2video'
COMFY_TEXT2VIDEO_WORKFLOW = _norm_workflow_default(os.environ.get('COMFY_TEXT2VIDEO_WORKFLOW'))
```

Reuse `_norm_workflow_default` (`config.py:37`). Add the matching commented env var to
`docker-compose.yml` alongside the `COMFY_IMAGE2VIDEO_WORKFLOW` example (~line 45).

### 2. `catalogue.py` — listing

Add `COMFY_TEXT2VIDEO_DIR` to the `from config import (...)` block (line 4-9) and:

```python
def list_text2video_workflows():
    return list_workflow_names(COMFY_TEXT2VIDEO_DIR)
```

next to `list_image2video_workflows()` (`catalogue.py:139`). `list_workflow_names` and
`resolve_workflow` (`catalogue.py:147`) are reused unchanged.

### 3. `app.py` — two routes + one template var

- Imports: add `list_text2video_workflows` (near `app.py:23`) and
  `COMFY_TEXT2VIDEO_DIR, COMFY_TEXT2VIDEO_WORKFLOW` (near `app.py:33`).
- `app.py:339` index context: `default_text2video_workflow=COMFY_TEXT2VIDEO_WORKFLOW,`.
- Listing route, next to `/api/image2video-workflows` (`app.py:432`):

```python
@app.route("/api/text2video-workflows")
@login_required
def api_text2video_workflows():
    return jsonify(list_text2video_workflows())
```

- Generation route, directly after `api_image2video` (`app.py:1127`). It is
  `api_image2video` minus the image plumbing:

```python
@app.route("/api/text2video", methods=["POST"])
@login_required
def api_text2video():
    """Run a text2video workflow from a prompt alone — no source image.

    Mirrors /api/image2video but loads from the text2video/ subdir, whose templates
    have no <INPUT_IMAGE>. An optional <REFERENCE_IMAGE> is still supported (pinned
    via /i2v-set-ref-image) for identity-preserving models; there is no first-frame
    fallback for it here, so a template using it needs a pinned reference.
    """
    data = request.get_json(force=True)
    raw_prompt = (data.get("prompt") or "").strip()
    if not raw_prompt:
        return jsonify({"error": "Prompt is required"}), 400
    prompt, _ = parse_loras_from_prompt(raw_prompt)

    ref_image_url = (data.get("ref_image") or "").strip()
    ref_image_path = None
    if ref_image_url:
        _, ref_image_path, err = resolve_input_image(ref_image_url)
        if err:
            return err

    available = list_text2video_workflows()
    workflow_name, err = resolve_workflow(
        data.get("workflow") or COMFY_TEXT2VIDEO_WORKFLOW, available, "text2video"
    )
    if err:
        return err

    server_address = data.get("server") or COMFY_SERVER
    server_os      = data.get("server_os") or COMFY_SERVER_OS

    vs, err = _parse_video_settings(data)
    if err:
        return err
    assert vs is not None

    err = output_storage_error()
    if err:
        return err

    job_id = start_generation_job(
        prompt, [], server_address, server_os, workflow_name,
        workflow_dir=COMFY_TEXT2VIDEO_DIR, input_reference=ref_image_path,
        duration=vs["duration"], frames=vs["frames"], fps=vs["fps"],
        video_width=vs["video_width"], video_height=vs["video_height"],
    )
    return jsonify({"job_id": job_id})
```

**Nothing in `generation_service.py` needs changing for the happy path.** With
`input_image=None` the `INPUT_IMAGE` mapping key is simply never set
(`generation_service.py:301`), and a template with no `<INPUT_IMAGE>` token passes the
unfilled-placeholder check at `generation_service.py:362`. `start_generation_job`'s
`is_video` classification (`generation_service.py:503`) already keys off the video
kwargs, so `/jobs` labels these `kind: "video"` for free.

### 4. `generation_service.py` — one robustness fix

`<REFERENCE_IMAGE>`'s fallback (`generation_service.py:336`) is
`mapping.get("INPUT_IMAGE", "")`. In a t2v run with no pinned reference that silently
becomes `""`, producing an obscure ComfyUI `LoadImage` failure. Make it fail clearly:

```python
else:
    fallback = mapping.get("INPUT_IMAGE")
    if not fallback:
        raise ValueError(
            "This workflow needs a <REFERENCE_IMAGE> — pin one with /i2v-set-ref-image"
        )
    mapping["REFERENCE_IMAGE"] = fallback
```

---

## Frontend

### 5. `static/js/state.js`

```js
t2vMode:                     false,   // /t2v — plain prompts generate video, not an image
currentText2VideoWorkflow:   null,
```

### 6. `static/js/chat.js`

**`runText2Video`** — next to `runImage2Video` (`chat.js:709`):

```js
function runText2Video(prompt, label) {
  state.iterationsFromSequence = false;
  return runGeneration(prompt, label || '', null, {
    text2video: { refImage: state.refImageUrl || null,
                  workflow: state.currentText2VideoWorkflow || DEFAULT_TEXT2VIDEO_WORKFLOW },
  });
}
```

(No `sendBtn.disabled` juggling — the `sendMessage` loop already owns that, unlike the
button-triggered `runImage2Video`.)

**`runGeneration` (`chat.js:1734`)** — four touch points:

| Line | Change |
|---|---|
| ~1739 | `const text2video = opts.text2video \|\| null;` |
| 1749 | add `\|\| text2video` to the `job` disjunction (so still-image `width`/`height`/`steps`/`extraPrompt` stay off the request and auto-face-detail at 1971 is skipped) |
| 1753 | `: text2video ? '/api/text2video'` in the endpoint ternary |
| 1803-1805 | include `text2video` in the video-settings spread, and send `ref_image` from `text2video.refImage` |
| 1837 | `const originPrompt = text2video ? (raw \|\| '') : job ? (state.imagePrompts[job.image] \|\| '') : (raw \|\| '');` — **required**: a t2v job has no `job.image`, so without this the produced video is recorded with an empty prompt and `/do-over`, `/i2v` and metadata edit all break on it |

Line 1795's `...(job ? { image: job.image } : {})` needs no guard — `JSON.stringify` drops
the `undefined` value.

**`sendMessage` (`chat.js:1364-1371`)** — the intercept:

```js
const ok = state.t2vMode ? await runText2Video(raw, label)
                         : await runGeneration(raw, label);
```

Apply the same swap to the `#macro` plain-step call at `chat.js:1329`, so a macro of bare
prompts obeys the mode. **Out of scope** (stays text-to-image): `/sequence-run` (the loop
is server-side, `/api/sequence-run`) and `/multi-prompt`.

**Header badge** — `updateHeaderStatus()` (`chat.js:61`): append a `🎬 t2v` marker while
`state.t2vMode` is on, so the mode is visible rather than something you rediscover by
generating a 5-second video by accident.

**Session persistence** — `doRecordSave()` (`chat.js:1151`) add
`t2vMode: state.t2vMode, text2videoWorkflow: state.currentText2VideoWorkflow`;
`restoreSession()` (`chat.js:1210`) restore both with the existing
`if (s.X !== undefined)` guard style so older session files still load.

### 7. `static/js/commands.js`

**Commands** — place them beside the `/i2v-workflow` block (`commands.js:1282`), i.e.
before the bare `addMessage('user', …)` at line 1674, so each echoes the user line itself:

```js
if (cmd === '/t2v') {
  addMessage('user', escapeHtml(raw), raw);
  state.t2vMode = !state.t2vMode;
  deps.updateHeaderStatus();
  if (state.t2vMode) {
    const wf = state.currentText2VideoWorkflow || DEFAULT_TEXT2VIDEO_WORKFLOW;
    addMessage('bot', `Text-to-video mode <strong style="color:#a78bfa">ON</strong> — prompts now generate video with <strong style="color:#a78bfa">${escapeHtml(wf || 'the default workflow')}</strong> (${state.currentVideoSettings.frames} frames @ ${state.currentVideoSettings.fps} fps). Type <code>/t2v</code> again to turn it off.`);
  } else {
    addMessage('bot', 'Text-to-video mode <strong>OFF</strong> — prompts generate images again.');
  }
  return;
}
```

plus `/t2v-workflow` and `/t2v-workflow-reset`, copied verbatim from the
`/i2v-workflow` / `/i2v-workflow-reset` blocks (`commands.js:1282-1308`) with
`renderWorkflowPicker({ url: '/api/text2video-workflows', … })` and
`state.currentText2VideoWorkflow`.

**Other sites in the same file:**

- `WORKFLOW_TYPES` (`commands.js:670`) — add a `{ label: 'Text → video', url: '/api/text2video-workflows', get/def/set }` row.
- `showChatSummary` (`commands.js:79`) — clone the "Image2video workflow" row block for
  Text2video, and add a `Text-to-video mode` row showing `ON`/`off`.
- `/help` `helpEntries` (`commands.js:1721`) — three `{ sig, desc }` entries next to the
  i2v ones.
- `newChat()` (`commands.js:566`) — `state.t2vMode = false;` and
  `state.currentText2VideoWorkflow = null;`.
- `/settings-save` / `/settings-restore` (`commands.js:2410` / `2446`) — snapshot and
  restore both new fields.

### 8. `static/js/autocomplete.js`

Three `SLASH_COMMANDS` entries beside the i2v ones (line 46):

```js
{ cmd: '/t2v',                  desc: 'toggle text-to-video mode (prompts generate video)', args: ''  },
{ cmd: '/t2v-workflow',         desc: 'choose a text2video workflow (no arg = picker)',     args: ' ' },
{ cmd: '/t2v-workflow-reset',   desc: 'reset the text2video workflow to its default',       args: ''  },
```

### 9. `templates/index.html:73`

```html
var DEFAULT_TEXT2VIDEO_WORKFLOW = {{ default_text2video_workflow | tojson }};
```

---

## Workflow template

The `text2video/` dir lives on **moria** at `~/comfy-workflows/text2video/` (bind-mounted
to `/app/workflows` — see the "Live Configuration" section of `CLAUDE.md`); it is not
present on this machine, so it must be created there.

Derive `minimax-h3-t2v-turbo-4step.json` from `/data/Downloads/minimax-h3-i2v-turbo-4step.json`:

1. Delete node `114` (`LoadImage`, holds `<INPUT_IMAGE>`) and node `117`
   (`ResizeImageMaskNode`, "Resize First Frame").
2. Delete the `first_frame` input from node `105:104` (`MiniMaxH3ImageToVideo`).
3. Keep everything else — nodes `118`/`119` (the `/32` snap expressions) are still
   consumed directly by `105:104` for width/height, so `<VIDEO_WIDTH>`/`<VIDEO_HEIGHT>`
   keep working. `<PROMPT>`, `<DURATION>` (node `105:111`) and the `<FPS>` in `CreateVideo`
   are untouched.

**Verify in the ComfyUI editor before wiring it up:** if `first_frame` turns out to be a
*required* input on `MiniMaxH3ImageToVideo`, ComfyUI will reject the graph. If so, check
whether the node pack ships a dedicated text-to-video class (e.g. `MiniMaxH3TextToVideo`)
and build the template around that instead — this is exactly the case the separate-dir
approach exists to handle. This step is the one genuine unknown in the plan.

Note the template hard-codes `fps: 24` in `CreateVideo` (node `105:91`) and the duration
math node `105:107` re-derives frame count from duration × 24 — worth replacing `24` with
`<FPS>` and dropping the unused `<FRAMES>` slot, or leaving it and accepting that
`/video-settings` fps is advisory for this workflow.

---

## Tests

- **`tests/test_app_routes.py`** — new `TestText2Video`, modelled on
  `TestImage2VideoSettings` (`tests/test_app_routes.py:606-691`): patch
  `app.start_generation_job`, `OUTPUT_VOLUME` in both `app` and `image_store`, and
  `catalogue.COMFY_TEXT2VIDEO_DIR`. Assert:
  - a prompt-only POST succeeds (no `image` field required) and forwards
    `workflow_dir=COMFY_TEXT2VIDEO_DIR` plus all five video kwargs;
  - an empty prompt → 400;
  - an unknown `workflow` → 400 (allow-list via `resolve_workflow`);
  - `ref_image` is forwarded as `input_reference` when supplied.
- **`tests/test_workflow_dirs.py`** — extend the listing coverage with `text2video`, and
  confirm the traversal guard applies (`workflow_name` of `../generation/x` is rejected).
- **`tests/test_generation_service.py`** — extend `ReferenceImageMappingTests` (line 425)
  with the new "no reference and no input image → clear ValueError" case.
- **JS**: no test. `chat.js`/`commands.js`/`state.js` have no Jest coverage (only the
  DOM-free `static/js/utils.js` does), and this change adds no pure transformation worth
  extracting. Still run `npm run test:js` to confirm nothing regressed.

---

## Verification

1. `node --check static/js/chat.js && node --check static/js/commands.js && node --check static/js/state.js && node --check static/js/autocomplete.js`
   — **mandatory** after every JS edit; the Edit tool silently corrupts straight quotes to
   curly ones (see "Known Pitfalls" in `CLAUDE.md`).
2. `./scripts/test-all` (python imports + unit tests + Docker container tests), then
   `npm run test:js` separately.
3. Create `~/comfy-workflows/text2video/` on moria and drop the derived template in;
   load the graph in the ComfyUI editor and hit Run once to prove it validates without a
   first frame **before** testing through the chatbot.
4. End-to-end in the running app:
   - `/t2v-workflow` → picker lists the new template; select it.
   - `/video-settings` → set duration/fps/resolution.
   - `/t2v` → confirms mode ON, header shows the badge.
   - Type a plain prompt → a video is generated and plays inline; `/jobs` shows it as
     `kind: video`; the video's stored prompt is the typed prompt (check via the metadata
     edit dialog or `/do-over`).
   - `/t2v` again → mode OFF; the same prompt now produces an image.
   - `/settings-save` → `/t2v` → `/settings-restore` restores the previous mode.
   - Reload after a chat save → `/session-load` restores mode and workflow.
5. `/last-sent` after a t2v run — confirm the submitted graph has no `LoadImage` node and
   no `first_frame` input.

---

## Housekeeping

- Per `CLAUDE.md`: copy this plan to `plans/t2v-text-to-video-mode.md` and commit it
  **before** implementing; rewrite it as `ADR/t2v-text-to-video-mode.md` afterwards.
- Add a short "Text-to-video (`/t2v`)" section to `CLAUDE.md` covering the `text2video/`
  dir, its placeholder set (`<PROMPT>`, `<DURATION>`, `<FRAMES>`, `<FPS>`,
  `<VIDEO_WIDTH>`, `<VIDEO_HEIGHT>`, optional `<REFERENCE_IMAGE>`; explicitly **no**
  `<INPUT_IMAGE>`), and the fact that `/t2v` does not affect `/sequence-run`.
- Deploy via the `push-to-portainer` skill once green — pausing for explicit approval
  before the Portainer redeploy, as that section of `CLAUDE.md` requires.
