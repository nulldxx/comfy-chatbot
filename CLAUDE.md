# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a self-hosted web chat interface for generating images with ComfyUI. Users type prompts (with optional `<lora:name:strength>` tags) into a chat UI; the app submits a ComfyUI workflow via the ComfyUI HTTP API, streams progress back via Server-Sent Events, and displays the resulting image inline in the conversation. It uses Flask with Gunicorn (gthread workers for SSE), is containerised with Docker, and is configured entirely via environment variables.

## Development Commands

### Building and Running
```bash
# Build and run locally (development)
docker-compose up --build -d

# Stop the application
docker-compose down

# View application logs
docker-compose logs -f comfy-chatbot
```

### Testing

#### Comprehensive Test Suite (Recommended)
```bash
# Run all tests: Python unit tests + Docker container tests
./scripts/test-all

# This runs the complete test suite:
# 1. Python import tests
# 2. Python unit tests
# 3. Docker container tests
# Note: JS tests (npm run test:js) are NOT included in test-all and must be run separately
```

#### Individual Test Components

**Python Unit Tests**
```bash
# Run import tests (verify all dependencies work)
python tests/test_imports.py

# Run unit tests
python -m pytest tests/test_simple.py -v

# Run all tests
python -m unittest discover tests/
```

**JavaScript Unit Tests**
```bash
# Run JS tests (Jest, tests/js/*.test.js)
npm run test:js
```

**Docker Container Testing**
```bash
# Run comprehensive Docker container test suite
./test-docker/test-container.sh

# This test script validates:
# - Docker build process
# - Container startup and health
# - Web interface accessibility
# - API functionality
# - Authentication system
```

### Release Management
```bash
# Create new release (increments patch version automatically)
./scripts/make-release

# Setup application for end users
./scripts/setup.sh  # Linux/macOS
./scripts/setup.ps1  # Windows PowerShell
```

### Local Development
```bash
# Install dependencies (optional for local testing)
pip install -r requirements.txt

# Run Flask development server (not recommended for production)
python app.py

# Production server (Gunicorn - used in Docker)
gunicorn --config gunicorn.conf.py app:app
```

## Architecture Overview

### Core Application Structure
- **app.py**: Main Flask application — chat API, generation threads, SSE streaming
- **gunicorn.conf.py**: Production WSGI server configuration with optimized worker settings
- **templates/**: HTML templates for web interface (index.html, login.html)

### Key Components
1. **Authentication System**: Session-based login with environment variable credentials
2. **Security**: Non-root container execution, secure session management
3. **API Endpoints**: RESTful endpoints for basic application functionality
4. **Health Checks**: Built-in health check endpoint for container orchestration

### Docker Multi-Stage Build
- **Builder stage**: Compiles Python packages with build dependencies
- **Runtime stage**: Minimal image with only runtime requirements
- Uses Python 3.11 slim base image for security and size optimization

### Configuration
Environment variables for deployment:
- `APP_USERNAME`: Authentication username (default: 'user')
- `APP_PASSWORD`: Authentication password (default: 'password')  
- `SECRET_KEY`: Flask session secret (change in production)

## Known Pitfalls

### Curly/smart quote corruption in JS files
The Edit tool can silently convert straight ASCII quotes (`'`, `"`) to Unicode curly/smart quotes (`'`, `'`, `"`, `"`) when editing JavaScript. These are not valid JS string delimiters and cause a full script parse failure, breaking everything silently. After any JS edit, verify with:
```bash
node --check static/js/chat.js
```
If curly quotes are found, fix with:
```bash
python3 -c "
lq='\\u2018'.encode(); rq='\\u2019'.encode(); sq=b\"'\"
data=open('static/js/chat.js','rb').read()
# replace only instances used as delimiters, not content
# inspect first: grep -P '[\\x{2018}\\x{2019}]' static/js/chat.js
"
```
Or use `sed -i "s/'/'/g; s/'/'/g" static/js/chat.js` to replace all curly single quotes (safe if the file has none intentionally).

## Development Guidelines

### Security Practices
- All routes except `/health` and `/login` require authentication
- Session-based authentication with configurable credentials
- Non-root user execution in container
- Secure session management with configurable secret key

### Performance Considerations
- Gunicorn multi-worker configuration scales with CPU cores
- Minimal Docker image for fast deployment
- Health checks ensure container reliability

### Testing Strategy
- Unit tests cover core application functions
- Import tests verify all dependencies work correctly in container environment
- Health check endpoint tests ensure proper API responses
- Mock authentication in tests using Flask test client sessions

### File Organization
```
/app.py                 # Main application logic
/templates/             # Jinja2 HTML templates  
/tests/                 # Unit tests and import verification
/scripts/               # Build, setup, and release automation
/test-docker/           # Docker container testing
```

### Deployment Notes
- Uses multi-stage Docker build to minimize image size
- Gunicorn configuration optimized for container deployment
- Health checks ensure container reliability in orchestrated environments
- Scripts provide automated setup and testing across platforms

## Workflow Template Parameters

Workflows stored in `~/dot-files/comfyui/` (and mounted at `/app/workflows`) are JSON templates with placeholder tokens that `workflow.py` replaces before submitting to ComfyUI. The replacement logic lives in `apply_placeholders()` and related functions in `workflow.py`.

### String placeholders (replaced as JSON-escaped strings)

| Placeholder | Description |
|---|---|
| `<PROMPT>` | The user's text prompt, with `<lora:...>` tags stripped out |
| `<LORA_1_NAME>` | Filename of the first LoRA (e.g. `my_lora.safetensors`), sourced from `<lora:name:strength>` tags in the prompt |
| `<INPUT_IMAGE>` | Base64-encoded source image for img2img, face-detailer, and inpainting workflows |
| `<INPUT_MASK>` | Base64-encoded B&W mask PNG for inpainting (white = area to repaint), uploaded separately via `/api/upload-mask` |
| `<INPUT_LAST_FRAME>` | Source image for the optional end frame in first-frame/last-frame image2video. When no end frame is designated it falls back to `<INPUT_IMAGE>` and the guide is bypassed (see below) |
| `<REFERENCE_IMAGE>` | Identity reference face for the LTX 2.3 face-ID image2video workflows (`LTXIdentityOverlapConditioning`). Pinned via the `/i2v-set-ref-image` command (sent as `ref_image` to `/api/image2video`); when none is pinned it falls back to `<INPUT_IMAGE>` (see the face-ID section below) |

### Numeric placeholders (replaced as bare JSON numbers, not quoted strings)

| Placeholder | Description |
|---|---|
| `<LORA_1_STRENGTH>` | Strength of the first LoRA (float, e.g. `0.8`), sourced from the `<lora:name:strength>` tag; defaults to `1.0` if omitted |
| `<DENOISE>` | Denoising strength for KSampler nodes (float 0.0–1.0); used in img2img workflows |
| `<DURATION>` | Video duration in seconds (float); image2video workflows. Set via `/video-settings` |
| `<FRAMES>` | Video frame count (int); image2video workflows. Set via `/video-settings` |
| `<FPS>` | Video frames per second (int); image2video workflows. Set via `/video-settings` |
| `<VIDEO_WIDTH>` | Video output width in px (int); image2video workflows. Set via `/video-settings`, kept distinct from the still-image resolution in `/image-settings` |
| `<VIDEO_HEIGHT>` | Video output height in px (int); image2video workflows. Set via `/video-settings`, kept distinct from the still-image resolution in `/image-settings` |

### LoRA handling detail

- Multiple LoRAs are supported: `<LORA_1_NAME>` / `<LORA_1_STRENGTH>`, `<LORA_2_NAME>` / `<LORA_2_STRENGTH>`, etc.
- LoRA slots with no corresponding `<lora:...>` tag in the prompt are filled with a sentinel value and then the entire LoRA node is removed from the workflow graph, with its model/clip outputs rewired to bypass it (`strip_lora_nodes()` in `workflow.py`).
- The pattern for matching lora tags in user input is `<lora:name:strength>` (case-insensitive); strength is optional and defaults to `1.0`.

### First-frame / last-frame detail (image2video)

- The LTX 2.3 image2video template optionally accepts a second image, `<INPUT_LAST_FRAME>`, so the model interpolates from the source (first) frame to a designated end frame, instead of only conditioning on the first frame.
- The end frame is conditioned by an **`LTXVAddGuide`** node (node `320:330`) pinned to the final frame (`frame_idx = -1`). LTX's `LTXVImgToVideoInplace` (used for the first frame) has **no** frame index, so it cannot place a last frame — `LTXVAddGuide` is required. The graph also contains the paired `LTXVCropGuides` (`320:284`) that strips the guide frames back out after sampling.
- The guide is toggled by a float placeholder, `<LAST_FRAME_STRENGTH>` (a bare JSON number fed to a `PrimitiveFloat` node, `320:325`): `1.0` = on, `0.0` = off. The UI designates the end frame with the 🎞️ button on an image (`makeLastFrameButton` / global `lastFrameUrl` in `chat.js`); `/api/image2video` accepts a `last_frame` image URL.
- When **no** end frame is supplied, `run_generation` strips the entire guide chain from the workflow graph after JSON parsing (`strip_last_frame_guide()` in `workflow.py`), removing nodes `270`, `320:325`, `320:331`, `320:332`, and `320:330`, and rewiring their downstream consumers directly to `LTXVConditioning` (`320:304`) and `LTXVImgToVideoInplace` (`320:296`). This is necessary because `LTXVAddGuide` at `strength=0.0` is **not** a true no-op — it still embeds the guide image into the latent at the last frame position, which causes a snap-back transition at the end of the video. Dummy placeholder values are still substituted before JSON parsing so the template parses cleanly, and the nodes are removed immediately after. When an end frame **is** supplied, strength is `1.0` and the second image drives the end frame.
- Wiring: `Load Last Frame` (`270`) → `Resize Last Frame` (`320:331`) → `LTXVPreprocess` (`320:332`) → `LTXVAddGuide` (`320:330`). The guide takes its conditioning from `LTXVConditioning` (`320:304`) and its latent from the first-frame `LTXVImgToVideoInplace` (`320:296`); its outputs feed the pass-1 concat (`320:318`), pass-1 guider (`320:314`) and `LTXVCropGuides` (`320:284`).

### Face-ID (identity-preserving) image2video detail

- Two LTX 2.3 workflows in `image2video/` preserve a character's identity from a **reference face image** via an `LTXIdentityOverlapConditioning` node (`layout: "overlap"`) plus a FaceID LoRA and a caption-rewriting `TextGenerate` node that reads the reference face and merges its visible appearance into the caption. Both keep the `ref_t2v: ` caption prefix the identity model expects (`"ref_t2v: <PROMPT>"`).
  - **`ltx23-faceid_i2v.json`** — *reference text-to-video*: an **empty** latent (`EmptyLTXVLatentVideo`), so the video content comes entirely from the prompt + the identity reference. **No first frame.** Frames are computed internally (`SimpleCalculatorKJ` = `((duration*fps)//8)*8+1`), so it has **no** `<FRAMES>` slot; it uses `<PROMPT>`, `<REFERENCE_IMAGE>`, `<DURATION>`, `<FPS>`, `<VIDEO_WIDTH>`, `<VIDEO_HEIGHT>`.
  - **`ltx23-faceid-firstlast_i2v.json`** — the same identity graph with the proven first-frame (`LTXVImgToVideoInplace`) + optional last-frame (`LTXVAddGuide` @ `frame_idx = -1`) sub-chain spliced between the empty latent and `LTXVConcatAVLatent`, ahead of the identity node. Adds `<INPUT_IMAGE>`, `<INPUT_LAST_FRAME>`, `<LAST_FRAME_STRENGTH>` to the set above. The optional-end-frame handling is the **existing** `strip_last_frame_guide()` path (no end frame → strength `0.0`, guide chain removed, `LTXVConcatAVLatent` falls back to the first-frame `LTXVImgToVideoInplace` latent).
- **The `<REFERENCE_IMAGE>` placeholder** is filled in `_run_generation_core` (`generation_service.py`), guarded on `"<REFERENCE_IMAGE>" in template`: a pinned reference (`input_reference`, uploaded) if supplied, else a fallback to the already-uploaded `<INPUT_IMAGE>` filename. So the reference is **override-with-fallback**: for the ref_t2v template the triggered image is the reference by default; for the first/last-frame template the **first frame** is the reference by default.
- **UI:** `/i2v-set-ref-image` pins the last chat image into `state.refImageUrl` (reset in `newChat`; cleared by `/i2v-set-ref-image-reset`). `runImage2Video` sends it as `ref_image` (only when it differs from the triggered image). Pick either template with `/i2v-workflow`.
- **⚠ Experimental composition:** in `ltx23-faceid-firstlast_i2v.json` the last-frame `LTXVAddGuide` and the identity overlap both add/crop guide frames (the graph's `LTXVCropGuides` uses the identity node's conditioning, not the AddGuide's). This combination must be **test-rendered in the ComfyUI editor**; if the last-frame guide isn't cropped cleanly, move the `AddGuide` to operate on the identity node's output latent/conditioning instead of before it, then re-export.

### Video settings detail

- `<DURATION>`, `<FRAMES>` and `<FPS>` are interdependent: `frames = duration × fps`. The `/video-settings` UI keeps them consistent — you lock one value (only one at a time) and editing either of the other two re-derives the third. The math lives in `utils.js` (`clampVideo` / `recomputeVideo`) and is unit-tested.
- Output is driven by `<FRAMES>` and `<FPS>` (both integers, fed to `PrimitiveInt` nodes); `<DURATION>` is the human-facing value and may round by a frame at the extremes.
- In the LTXV image2video template, the latent length math node consumes the Frames primitive as `frames + 1` (the extra conditioning frame), the Frame Rate primitive feeds the conditioning/audio/CreateVideo nodes, and the Duration primitive is informational.
- The Wan 2.2 14B image2video template (`image2video/wan22_14B_i2v.json`) uses the same placeholders. Its `<FPS>`/`<DURATION>` feed `PrimitiveFloat` nodes (not `PrimitiveInt`) — injecting a bare integer is still valid JSON. Length is driven by a `<FRAMES>` `PrimitiveInt` (node `129:164`) through a `Math Expression (length)` node (`129:163`) as `frames + 1`, mirroring LTXV; the FPS primitive also feeds `CreateVideo`, and the Duration primitive is informational. This template has **no** audio nodes.
- **Video resolution**: `/video-settings` also sets `<VIDEO_WIDTH>`/`<VIDEO_HEIGHT>` (stored on `currentVideoSettings.width`/`.height`, default `1280×720`), sent to `/api/image2video` as `video_width`/`video_height`. This is deliberately **separate** from the still-image resolution in `/image-settings` (which flows through `apply_resolution`/`currentResolution`) because video models have very different size constraints. Dimensions are clamped to 64–2048 and snapped to a multiple of 16 (`clampVideo` in `utils.js`). In templates they replace the width/height primitives directly: the Wan templates' `WanImageToVideo` width/height (node `129:98`), and the LTX template's separate `Width`/`Height` `PrimitiveInt` nodes (`320:312`/`320:299`). The Wan and image-resolution paths don't collide because image2video never sends the still `width`/`height`, so `apply_resolution` isn't called for it.
- **Audio toggle**: `/video-settings` has an Audio checkbox stored on `currentVideoSettings.audio` (default `true`). It is purely client-side — when off, `buildVideoPrompt()` (`utils.js`) drops the `Audio: <audio>` segment that `/video-sequence` folds into a video prompt, so audio-less workflows (e.g. the Wan template) aren't fed audio cues they ignore. It does not alter the workflow graph; audio-capable workflows still generate their own audio track regardless.

### Validation

`fill_placeholders_for_validation()` substitutes dummy values (`1.0` for float slots including `<LAST_FRAME_STRENGTH>`, `1` for the integer video slots, `"placeholder"` for string slots) so a template file can be parsed as valid JSON during startup validation.

## Encrypted volumes & filesystem checks (`/fscheck`)

Images live on up to two LUKS-encrypted volumes (auto-created as ext4), mounted on
the host by the root **archive-agent** (`packaging/agent/archive-agent`) over a Unix
socket because the container is unprivileged:

- **archive** volume (`ARCHIVE_VOLUME`) — mounted on demand only during an archive op.
- **output** volume (`OUTPUT_VOLUME`) — mounted persistently at `IMAGES_DIR` for the container's whole life.

`/fscheck` runs `e2fsck -f -y` (force + auto-repair everything) on these. Because
`e2fsck` refuses a **mounted** filesystem, the two are handled differently:

- **Archive** volume — normally unmounted, so `/fscheck` checks it **live**, via the
  agent's `fsck` action (`cryptsetup open` without mount → `e2fsck` → `cryptsetup
  close`), serialised under `archive_lock` so a check and an archive op never race.
- **Output** volume — checked at **container startup** by `docker-entrypoint.sh`
  calling `python -m agent_client check-output` (best-effort; never blocks startup),
  just before `mount-output`. The output mount lives in the **host** mount namespace
  (agent runs `MountFlags=shared`) so it survives container restarts; `check-output`
  therefore **unmounts first** (safe — the app isn't serving yet and `mount-output`
  remounts on the next line) so e2fsck isn't refused on a mount left over from an
  unclean stop. The result is written to `OUTPUT_FSCHECK_RESULT` and `/fscheck`
  surfaces it rather than re-checking live. The archive path likewise unmounts a
  stale mount (under `archive_lock`) before its live check.

Flow: `/fscheck` (command in `commands.js`) → `POST /api/fscheck` returns a `job_id`
→ streamed over the existing `/api/progress/<job_id>` SSE (via `start_background_job`
in `generation_service.py`), so a slow e2fsck never trips the gunicorn worker timeout.

Config (`config.py`): `OUTPUT_FSCHECK_RESULT` (default `/tmp/comfy-output-fscheck.json`,
must be **outside** `IMAGES_DIR` since the check runs before the output mount) and
`FSCK_TIMEOUT` (client socket timeout, must exceed the agent's `E2FSCK_TIMEOUT_SECONDS`).
The agent's `fsck` action needs `e2fsprogs` (declared in `packaging/deb/control.template`).

**Caveat:** `e2fsck -fy` auto-answers *yes* to every repair — thorough, but severe
corruption can mean data loss without a prompt. This is the deliberate, chosen policy
for an unattended appliance.

## Host access to the archive volume (`m` → `/api/host-mount`)

The container is the **sole owner** of the encrypted archive volume. External host
access (to manage the archive over samba) goes **through the container**, never by
mounting the volume directly — two independent mounts of one ext4 filesystem
corrupt it and lose data (the historic `m` bug).

The host script `~/dot-files/scripts/m` (in the **dot-files** repo) is now a thin
API client: it reads `APP_USERNAME`/`APP_PASSWORD` and the published port from
`~/dot-files/docker-compose/comfy-chatbot.yml`, logs in for a session cookie, then
calls:

- `POST /api/host-mount` — under `archive_lock`, asks the agent (`host-mount`
  action) to bind the **single** archive mount onto the agent-configured
  `HOST_MOUNT_DIR` (`/run/media/private/ben/secure`, owned by `HOST_MOUNT_USER`).
  Returns `{ok, mountpoint}`. `m` then starts `samba` / `docker-snap-alt`.
- `POST /api/host-unmount` — pops the host bind and closes the volume (back to
  unmounted-at-rest, so `/fscheck` works). `m -u` stops the containers first.
- `GET /api/host-status` — reports `{configured, host_mounted, open}`.

**Exclusive mode:** while the host mount is active, `/api/archive` and `/api/fscheck`
refuse (HTTP 409, "run `m -u` first"), gated by `_host_mount_active()` in `app.py`
which reads the agent's enhanced `status` action (`host_mounted`). Belt-and-suspenders:
the agent's `fsck` also refuses whenever the backing file is attached to a loop
device (`losetup -j`), catching any use its own mountpoint checks can't see.

The invariant that makes this safe: the LUKS volume is decrypted and ext4-mounted
**exactly once**; the container (`/app/archive`) and host samba (`HOST_MOUNT_DIR`)
are both **bind mounts** of that one mount — never a second `cryptsetup`/`zuluCrypt`
open. Consequence: host access **requires the container running** (`m` preflights
`/health`); the passphrase (`SECRET_KEY`) never leaves the container/agent.

Deploying this needs the updated `archive-agent` on the host (the `host-mount`/
`host-unmount`/`status` actions ship in `packaging/agent/archive-agent`; the
`HOST_MOUNT_*` keys have safe defaults so `/etc/archive-agent.conf` need not change).

## Live Configuration (Host Machine)

The `docker-compose.yml` in this repo is an **example only**. The live deployment uses:

- **Docker Compose file**: `~/dot-files/docker-compose/comfy-chatbot.yml`
- **ComfyUI workflows**: `~/comfy-workflows/` on the host `moria` (bind-mounted into the container at `/app/workflows` per that compose file; image2video templates live in `~/comfy-workflows/image2video/`)

## Releasing & deploying (the `push-to-portainer` skill)

After **successfully completing a feature** (change implemented, tests/build passing),
run the **`push-to-portainer`** skill to release and deploy it: it commits & pushes to
`main`, cuts a release with `scripts/make-release`, watches the GitHub Actions build
(fixing any failures), and then redeploys the live `comfy-chatbot` stack on the Portainer
server (moria) to pull the new image.

**Always get explicit confirmation from the user before the Portainer update (Stage 4).**
The redeploy restarts the live service, so pause after the release build is green and ask
the user to approve before running the redeploy — never update Portainer automatically.