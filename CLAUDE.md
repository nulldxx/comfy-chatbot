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

### User-changeable login password (`/change-password`)

The login **password** is user-changeable at runtime via the `/change-password` slash
command (also reachable from `/settings`), which POSTs `{current, new, confirm}` to
`@login_required POST /api/change-password` in `app.py`. The username stays env-driven.

Storage lives in `auth_store.py`, which writes a salted, memory-hard **scrypt** hash
(implemented directly against `hashlib.scrypt` — the pinned Werkzeug 2.3.7 predates
`generate_password_hash(method="scrypt")`, so we don't depend on werkzeug's helper) to
`/app/workflows/.auth.json` (`COMFY_WORKFLOW_DIR/.auth.json`), written atomically with
`0600` perms. This path is chosen deliberately: it's the only writable, **non-encrypted**
mount that survives a push-to-portainer redeploy — the encrypted `IMAGES_DIR` is ruled
out, and the container's writable layer is wiped on redeploy.

Precedence (`auth_store.verify_password`): once a password has been set via the UI, the
stored hash is **authoritative** and the env `APP_PASSWORD` **no longer works** (it is
only a first-boot bootstrap). **Reset** = delete `~/comfy-workflows/.auth.json` on the
host to revert to `APP_PASSWORD`.

Notes: `SECRET_KEY` is intentionally **never** touched here (it doubles as the LUKS
passphrase for the encrypted volumes). The hash file is dot-prefixed so workflow `*.json`
globs skip it, and it sits outside `IMAGES_DIR` so the `/api/settings-backup` bundle
(which only zips `IMAGES_DIR`) never carries password hashes off the box. Login/change
are still cleartext-in-transit (no TLS/CSRF layer) — the improvement is at-rest hashing
plus user self-service, not transport security.

### Password-derived LUKS keys (re-keying)

**Implemented** (see `ADR/archive-rekeying.md`). The LUKS passphrase for **both** the
archive and output volumes now depends on the login password, so a leaked compose file
(`SECRET_KEY`) alone no longer decrypts the archives.

- **`crypto_key.py`** — `derive_passphrase(secret_key, password)` = `sha256(secret_key
  \x00 password)` (**pinned**); `effective_passphrase(...)` returns `SECRET_KEY` until a
  UI password is set (bootstrap), the derived value once set, and raises
  `VolumeLockedError` if set-but-not-logged-in. `app.effective_passphrase()` wraps it and
  is used at every volume-open site (`api_archive`, `api_fscheck`, `api_host_mount`).
- **In-memory password** (`auth_store.set_session_password`/`current_password`) — the
  plaintext login password, held in memory after login, never persisted; gone on restart.
- **Re-key on change** (`app._rekey_and_commit`, `/api/change-password`): under
  `archive_lock`, header-backup → add-key old→new (+ recovery on first set) → **commit**
  (save hash + set in-memory password) → remove-key old. Password is persisted only after
  the new key is proven to open every volume, so "password changed ⟺ new key unlocks".
- **Agent actions** (`packaging/agent/archive-agent`): `add-key`, `remove-key` (refuses
  to drop the last working key), `header-backup` — keyslot-only, safe while mounted.
- **Lazy output mount**: once a password is set the output volume no longer auto-mounts
  at startup (`agent_client` skips it); it mounts on first login
  (`app._lazy_output_check_and_mount`, fsck-then-mount with the derived passphrase).
  `login_required` forces a fresh login when a password is set but the process holds none
  (stale cookie after restart). Consequence: **no images/sessions until login** after a
  restart.
- **Storage readiness (`/api/storage-status`)**: the lazy mount runs on a background
  thread, so for the first seconds after login `IMAGES_DIR` is an empty stand-in
  directory. Every endpoint backed by it — macros, aliases, default-macro, chats,
  images, settings-backup — is wrapped in `@requires_output_storage` (`app.py`) and
  answers **503** until the mount lands, rather than reading an empty dir (which the UI
  cached as "no macros" for the life of the page — you had to sign out and in again) or
  writing under the mountpoint (which then vanishes beneath the mount). The client polls
  `/api/storage-status` (`{encrypted, ready}`) at page load and only then fetches the
  catalogues and resumes a running sequence run (`chat.js`), showing a "🔒 Unlocking
  encrypted storage…" bubble while it waits.
- **Recovery keyslot**: a one-time random recovery passphrase is generated on the first
  password set, added to a spare keyslot on each volume, and shown **once** in the
  change-password UI (`commands.js`) — never stored server-side. Forgotten password +
  lost recovery key = archive unrecoverable.
- **`~/dot-files/scripts/m`** (separate repo) prompts for the password when the compose
  `APP_PASSWORD` is superseded, priming the app's in-memory password for host-mount.

### Idle session lock (`idle_lock.py`)

The in-memory password and the mounted output volume used to persist for the whole
life of the process, so a box left alone overnight sat decrypted. After
`IDLE_TIMEOUT_SECONDS` (default `7200`; `0` disables) with no activity, the app logs
everyone off, forgets the password and closes both volumes. **A restart, an idle
lockdown or a `/logoff` means the next request lands on `/login`.**

- **`/logoff` (and the header *Sign out* link) do the same thing on demand** — see the
  "On-demand lockdown" section below. The lockdown body lives in `app._lock_down()`;
  `_idle_lock_down()` is now just that plus the idle-specific log line.

- **`idle_lock.py`** holds one activity timestamp plus a watchdog daemon thread that
  ticks every `TICK_SECONDS`. `configure(timeout, on_idle, is_busy)` wires it from
  `app.py`; it never imports `app`, so it is testable standalone. A watchdog rather
  than a re-armed `threading.Timer` (the auto-purge pattern) because activity fires
  on *every* request and re-arming would spawn a thread each time.
- **Started lazily, never at import** — gunicorn sets `preload_app = True`, so the
  module is imported pre-fork and threads don't survive `fork()`.
  `mark_activity()` calls `ensure_started()`, creating it in the worker.
- **Activity is marked inside `login_required`**, not a `before_request` hook — the
  Docker healthcheck polls `/health` unauthenticated and would otherwise reset the
  clock forever.
- **`app._idle_busy()`** suspends the clock while any job is non-terminal. Essential:
  a `sequence-run` drives its loop in a daemon thread for up to
  `COMFY_POLL_TIMEOUT_SECONDS` (4h) with zero incoming requests, so a
  request-timestamp-only clock would unmount the output volume from under it.
- **`app._idle_lock_down()`** takes `archive_lock` then `output_mount_lock`, both
  **non-blocking** — it returns `False` and is retried next tick rather than leaving
  a half-locked state. It force-unmounts the host bind (`m`/samba) if active, closes
  the archive, unmounts output, then clears the password and bumps the auth epoch.
- **Auth epoch** (`app.current_auth_epoch`/`bump_auth_epoch`) — session cookies are
  signed with the stable `SECRET_KEY` and carry no expiry, so a bump is the only way
  to revoke them all. Needed because `login_required`'s existing "password set but
  none in memory" check doesn't fire in bootstrap mode. Cookies with no epoch key
  default to `0` so pre-upgrade sessions stay valid until the first lockdown.
- **Recovery is free**: `login()` already calls `_start_lazy_output_mount()`, which
  is idempotent and re-opens the output volume.
- **Caveat**: the idle clock tracks *app* activity, which samba traffic does not
  touch — a long unattended copy over the `m` host mount can be cut off. Re-run `m`.

### On-demand lockdown (`/logoff`, `/logout`)

`/logoff` re-locks the appliance without waiting for the idle timeout: it closes both
encrypted volumes, forgets the in-memory password, revokes every session cookie and
sends the browser to `/login`. It is the same `app._lock_down()` the idle watchdog runs.

- **`/logout` was the trap this fixes.** It used to pop the session cookie and *nothing
  else* — both volumes stayed mounted, the plaintext password stayed in memory (so the
  LUKS key was still derivable) and, because it never bumped the auth epoch, every other
  outstanding cookie stayed valid. "Sign out" looked like it secured the box and didn't.
  It now performs the full lockdown, so there is exactly one way to leave.
- **`POST /api/logoff`** (`@login_required`) is the real implementation; `GET /logout`
  is the no-JS/bookmark fallback running the same sequence. The header link
  (`templates/index.html`, `#sign-out`) is wired in `chat.js` to call
  `handleSlashCommand('/logoff')` rather than navigate, so a refusal shows up as a chat
  message instead of a silent bounce.
- **Refuses rather than half-doing it**, in both directions: while any job is
  non-terminal (`_logoff_refusal()`, the same `TERMINAL_STATUSES` check `_idle_busy()`
  uses — a sequence run can go 4h with no request, and unmounting the output volume
  under it loses its work), and when `_lock_down()` returns `False` because
  `archive_lock`/`output_mount_lock` is held. Both answer **409** and **leave the
  session signed in** — signing out of a still-open appliance is the very thing being
  removed.
- **`idle_lock.note_locked_down()`** is called at the end of `_lock_down()` so the
  watchdog doesn't repeat the work — and mislog it as an idle lockdown — when the clock
  later runs out. `mark_activity()` clears the flag again on the next login.
- **Cost:** every sign-out now pays the lazy output re-mount on next login (the
  "🔒 Unlocking encrypted storage…" bubble). That is the point, but it makes signing
  out non-trivial where it used to be free.
- **Test note:** a lockdown bumps the process-global auth epoch, which revokes the
  forged `sess['authenticated'] = True` sessions the rest of the suite relies on. Any
  test that triggers one must save and restore `app._auth_epoch` (see
  `tests/test_logoff.py` and `tests/test_idle_lock.py`).

### Media right-click menu & per-image seeds (`mediamenu.js`, `seed_store.py`)

Right-clicking any generated image or video opens an in-app menu — **Save**, **Copy**,
**Copy seed** — instead of the browser's. Two motivations: `.img-wrap` had run out of
edges (twelve `img-*` hover buttons, one per corner and side), and `/getseed` could only
ever offer the *most recent* seed, which is rarely the image you are looking at.

- **One delegated `contextmenu` listener on `document`** (`initMediaMenu`, called from
  `chat.js`), not per-render wiring. The `/images/...` URL is already the identity key
  everywhere, so a single handler covers chat bubbles, the `/review-all` and
  sequence-review grids, the slideshow and the lightbox — including `grids.js` and
  `slideshow.js`, which render media without going through `appendChatImage`. Gating on
  the `/images/` prefix leaves the native menu over mask/crop editor canvases,
  `/references` thumbs and `/references-file/` previews. **Shift+right-click** always
  falls through to the browser menu (a `<video>`'s playback-speed and PiP entries live
  there).
- **Copy image needs a secure context.** `navigator.clipboard.write` exists only under
  HTTPS or localhost, and the appliance is normally reached over plain HTTP on a LAN
  address, so the row is feature-detected and becomes **Copy image address**
  (`writeText`) when unavailable. When it *is* available the blob is transcoded through a
  canvas to PNG first — Chrome and Firefox accept only `image/png` in a `ClipboardItem`,
  but the gallery also holds `.webp`/`.jpg`. Video is address-only. The `ClipboardItem`
  is built from a **promise**, not an awaited blob, so `write()` is called synchronously
  inside the click handler (Safari discards the user gesture otherwise); older Firefox
  rejects that form and falls back to copying the address.
- **`seed_store.py`** is the per-image seed index: a flat `{filename: "seed-as-string"}`
  map at `SEEDS_FILE` (`.seeds.json` under `IMAGES_DIR`). Dot-prefixed, and
  `select_images()` filters on `MEDIA_EXTS`, so it is never listed by `/api/images` nor
  swept into an archive. Read by `GET /api/image-seed/<filename>`, which the menu calls
  on **open** so the row can say up front whether there is a seed to take. Seeds are
  strings on the wire for the same reason `/api/last-seed` uses them — a 64-bit seed does
  not survive a JS `Number`.
- **No in-memory cache, deliberately.** `IMAGES_DIR` is the lazily-mounted encrypted
  output volume, so a dict loaded before the mount lands would cache the empty stand-in
  directory for the life of the process — the exact failure `@requires_output_storage`
  exists to prevent. The file is small and generations are seconds apart.
- **Written for every job kind.** `_run_generation_core` now reads `collect_seeds()`
  unconditionally into `effective_seed` and calls `record_seeds()` after the output files
  are renamed, so face-detail, upscale, i2i, inpaint, remove and each sequence-run shot
  all get a recorded seed — none of which `/getseed` ever covered. `set_last_seed` stays
  gated on `track_seed`, so `/getseed` semantics are unchanged. Best-effort: a failed
  write must never cost the user an image.
- **Bounded and self-healing.** Past `SEED_STORE_MAX` (5000) the store drops entries whose
  image is gone, then oldest-first — so `/api/archive`, which *moves* files out of
  `IMAGES_DIR`, needs no hook. `DELETE /api/images/<name>` and `DELETE /api/images` prune
  eagerly as well.
- **Copy seed reuses `/getseed`'s pipeline entirely**: it sets the same one-shot
  `state.reuseSeed`, consumed by `runGeneration` and cleared after the response is
  accepted, so it inherits the same scope — **t2i / i2v / t2v only** — and
  `/getseed-reset` clears it. `newChat()` now resets it too (it previously did not, so a
  pin leaked into the next chat).
- **Not retroactive**, and **archived images lose their seed**: the map is a convenience
  index, not provenance. iOS long-press is unsupported — desktop and Android Chrome both
  fire `contextmenu`, iOS Safari does not, and a synthetic touch-hold would collide with
  the pinch/swipe handlers in `lightbox.js` and the drag-sort in `grids.js`.

### Generation progress bars (`comfy_progress.py`)

The generation bubble used to show an indeterminate marquee for the whole render.
ComfyUI publishes step counts **only** on its WebSocket (`ws://<host>/ws?clientId=…`) —
`GET /history/<prompt_id>`, which `poll_status` polls, stays empty until the prompt
finishes — so `ProgressListener` reads that feed on a per-job daemon thread and reduces
it to a snapshot the job thread reads. See `ADR/generation-progress-bars.md`.

- **It rides the existing tick.** `_run_generation_core` already emitted
  `{"type":"tick"}` every ~2s from `poll_status`'s `"."` heartbeat, and nothing consumed
  it; the snapshot is merged into that event, so **the bar adds no SSE events** and
  `poll_status` is untouched. A bare `{"type":"tick"}` is exactly what a run with no
  listener sends, so nothing regresses when the feed is unavailable.
- **`client_id` was already on the wire.** `ComfyServer` generates one and sends it with
  every `POST /prompt`; ComfyUI routes that prompt's messages to the socket that
  connected with the same `clientId`. The listener is started **before** the submit —
  ComfyUI buffers nothing, so a socket opened afterwards misses the opening messages —
  and `bind(prompt_id)` supplies the filter once the submit returns.
- **Percent is weighted by node cost**, not by node count: `(finished weight + running
  node's step fraction × its weight) / total weight`, clamped **non-decreasing**
  (multi-pass graphs re-run their samplers). `node_weights_for()` splits the submitted
  graph into three tiers — the **samplers own 85%** (`SAMPLER_SHARE`) of the bar between
  them, divided in proportion to their step counts; VAE decode / video encode /
  save-video are **5×** (`HEAVY_WEIGHT`) an ordinary node; everything else is 1. A node
  counts as a sampler iff its `class_type` matches `/sampler/i` **and** it takes a latent
  — that second test is what keeps `KSamplerSelect`, which merely picks a sampler, out of
  the 85%. Steps come from the sampler's own `steps` input, else a bounded walk upstream
  to the `BasicScheduler`/`LTXVScheduler` feeding its `sigmas`, else 20; because the
  weights are read off the **submitted** graph, the `/video-settings` steps override and
  the accelerator LoRAs' 4/8 steps are already baked in. A graph with no recognised
  sampler falls back to the old uniform accounting. The shares are estimates, not measured
  timings, so the caption (`Sampling — step 12/20 · node 7/23`) still carries the honest
  number.
- **`progress_state` is not the whole graph.** Against ComfyUI 0.34.2 it lists only nodes
  it holds progress records for — one that arrived via `execution_cached` never appears.
  Its finished nodes are **unioned** into the known set; replacing it made progress drop
  back on every such message, visible only as a frozen bar because the monotonic clamp
  swallowed the regression.
- **Telemetry, never a dependency.** No `websocket` module, a refused upgrade, a read
  error — `latest()` returns `None` and the UI keeps the marquee; the construction is
  `try/except`-wrapped at the call site too. `COMFY_WS_PROGRESS=0` disables it.
- **Replay collapses stale ticks** (`api_progress`): a tick is volatile state, not
  history, so a reattaching client gets only the newest one instead of thousands.
- **Scope**: every ComfyUI kind, since they all funnel through `_run_generation_core` —
  t2i, i2v, t2v, face-detail, upscale, i2i, inpaint, remove, and each sequence-run shot
  (`openShell` gained the bar it never had). `/fscheck` and `/api/archive` have no step
  data and keep the marquee; `/jobs` still shows only a status label.
- Client helpers `progressPercent`/`progressCaption` are pure and live in `utils.js`
  (unit-tested); `.determinate` on `.progress-bar-wrap` is what kills the CSS animation.

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

### Session State
- When adding a new user-facing setting to `state` (`static/js/state.js`), consider whether it should persist with the chat session: if so, wire it into the session save/restore (`saveSession`/`restoreSession` in `chat.js`), the `/settings-save`/`/settings-restore` snapshot stack (`commands.js`), and the `newChat` reset — otherwise it will silently revert on reload.

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

### Plans and ADRs
- Plans should always be saved as markdown, with a meaningful name, and committed to plans/ before implementation
- Once implementation is complete, the plan should be rewritten to reflect how it was implemented at a high level and saved in ADR/

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
| `<REFERENCE_IMAGE_1>` | Reference image slot 1 — the **mandatory primary reference** (identity reference for the LTX 2.3 face-ID workflows, `LTXIdentityOverlapConditioning`; MiniMax H3 image 1) **with fallback**: when unset it falls back to `<INPUT_IMAGE>`, and if there's no source image either (e.g. text2video) the job **errors**. Set via the `/references` table (see the References section below) |
| `<REFERENCE_IMAGE_2>` … `<REFERENCE_IMAGE_9>` | Reference image slots 2–9 (MiniMax H3 R2V). Optional — when unset the loader node is stripped and the consumer's input dropped, so an R2V graph can run on any subset of the extra images |
| `<REFERENCE_VIDEO_1>` / `<REFERENCE_VIDEO_2>` / `<REFERENCE_VIDEO_3>` | Reference video slots 1–3 (MiniMax H3 R2V). Optional; stripped when unset. A gallery clip or an uploaded `/references-file/` URL. Each clip carries **two selectable tracks** — see the References section |
| `<REFERENCE_VIDEO_AUDIO_1>` … `<REFERENCE_VIDEO_AUDIO_3>` | The **audio track of reference video n**, never a separately uploaded file. Only used by the two-node template convention (a second loader pointed at the same clip); in the single-node convention the token is absent and the loader's AUDIO output is disconnected instead. Optional; stripped when that clip's audio box is unticked |
| `<REFERENCE_AUDIO_1>` / `<REFERENCE_AUDIO_2>` / `<REFERENCE_AUDIO_3>` | Standalone reference audio (MiniMax H3 R2V), slots 1–3. Optional; stripped when unset. Uploaded `/references-file/` audio |

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
  - **`ltx23-faceid_i2v.json`** — *reference text-to-video*: an **empty** latent (`EmptyLTXVLatentVideo`), so the video content comes entirely from the prompt + the identity reference. **No first frame.** Frames are computed internally (`SimpleCalculatorKJ` = `((duration*fps)//8)*8+1`), so it has **no** `<FRAMES>` slot; it uses `<PROMPT>`, `<REFERENCE_IMAGE_1>`, `<DURATION>`, `<FPS>`, `<VIDEO_WIDTH>`, `<VIDEO_HEIGHT>`.
  - **`ltx23-faceid-firstlast_i2v.json`** — the same identity graph with the proven first-frame (`LTXVImgToVideoInplace`) + optional last-frame (`LTXVAddGuide` @ `frame_idx = -1`) sub-chain spliced between the empty latent and `LTXVConcatAVLatent`, ahead of the identity node. Adds `<INPUT_IMAGE>`, `<INPUT_LAST_FRAME>`, `<LAST_FRAME_STRENGTH>` to the set above. The optional-end-frame handling is the **existing** `strip_last_frame_guide()` path (no end frame → strength `0.0`, guide chain removed, `LTXVConcatAVLatent` falls back to the first-frame `LTXVImgToVideoInplace` latent).
- **The `<REFERENCE_IMAGE_1>` placeholder** is filled in `_run_generation_core` (`generation_service.py`), guarded on `"<REFERENCE_IMAGE_1>" in template`: reference image slot 1 (uploaded via `ComfyServer.upload_media`) if supplied, else a fallback to the already-uploaded `<INPUT_IMAGE>` filename (and if there's no source image either, the job errors). So slot 1 is the **mandatory override-with-fallback** reference: for the ref_t2v template the triggered image is the reference by default; for the first/last-frame template the **first frame** is the reference by default. Image slots 2–9 are optional/strippable. See the **References** section below for the full multi-slot scheme.
- **UI:** `/references` opens a table whose rows are drop targets (images 1–9, videos 1–3 with per-clip video/audio track checkboxes, standalone audios 1–3), each with an on/off switch; `runImage2Video`/`runText2Video` send a `references` object to `/api/image2video`/`/api/text2video`. Only **image 1** is used by the LTX face-ID templates (and it's suppressed when it equals the triggered image, so the backend's `<INPUT_IMAGE>` fallback applies). Pick a template with `/i2v-workflow`.
- **⚠ Experimental composition:** in `ltx23-faceid-firstlast_i2v.json` the last-frame `LTXVAddGuide` and the identity overlap both add/crop guide frames (the graph's `LTXVCropGuides` uses the identity node's conditioning, not the AddGuide's). This combination must be **test-rendered in the ComfyUI editor**; if the last-frame guide isn't cropped cleanly, move the `AddGuide` to operate on the identity node's output latent/conditioning instead of before it, then re-export.

### References (`/references`) detail

`/references` replaced the single-slot `/i2v-set-ref-image` with a table of reference
assets for video workflows: **9 images + 3 videos + 3 standalone audios** (MiniMax H3
R2V), capped at **12 files in total**, of which LTX face-ID uses only image 1. Each row
has an **on/off switch** so a reference can be parked without being deleted.

A reference **video is one clip with two usable tracks** — ComfyUI's VHS
`Load Video (Upload)` node emits AUDIO alongside IMAGE — so each video row has **two
checkboxes**, video and audio, both ticked by default. There are no separate
"video-paired audio" upload slots; that audio was never an independent asset, and
pairing it by array index was only ever a convention nothing enforced.

- **State**: `state.references = { images: [9], videos: [3], videoTracks: [3], audios: [3],
  enabled: {images: [9], videos: [3], audios: [3]} }`, where `videoTracks[i]` is
  `{video, audio}` for `videos[i]` and `enabled[key][i]` is the row's on/off switch
  (`state.js`, `newReferences()`/`cloneReferences()`, slot counts in
  `REFERENCE_SLOT_COUNTS` — which deliberately excludes `videoTracks`, being the source
  of truth for "array of URL-or-null" groups — defaults in `REFERENCE_TRACK_DEFAULT`,
  cap in `REFERENCE_MAX_FILES`). Persisted with the chat session (`saveSession`/`restoreSession`),
  the `/settings-save` stack, and reset in `newChat` — none of which needed changing when
  the shape changed, because they all funnel through `cloneReferences`. That function
  migrates the pre-expansion shape (a 3-image array + scalar `video`/`videoAudio`/`audio`)
  and the old `videoAudios` array (a filled entry becomes that clip's audio track; an
  orphaned one, with no video in the slot, is dropped). An old session's `refImageUrl`
  lands in `images[0]`. Accepted loss: the old table could pair a video with a
  *different* audio file — that can't survive the one-clip model.
- **Per-row enable switch**: every row carries an on/off switch (`references.enabled`)
  that **parks** a reference — the URL and, for a video, its track ticks are kept, but the
  row is charged **nothing** against the cap and sent as **empty**, so switching sets in
  and out doesn't mean re-uploading. Absent flags read as *on*, so a session or payload
  from before the switches existed behaves exactly as it did (`referenceSlotEnabled`).
  Attaching a file re-enables a parked row (dropping a file is an unambiguous "I want
  this"); `✕` resets the flag, so an empty slot never carries hidden state. Off is
  distinct from a video's **inactive** (on, but neither track ticked) — the row caption
  says which. Purely client-side: `referencesForRun` (`chat.js`) masks an off row to
  `null`, so the wire format and `_resolve_references` are untouched.
- **12-file cap**: a video charges **once per ticked track**, so a both-tracks clip costs
  2 and an untouched-but-inactive clip costs 0; a switched-off row costs 0 whatever it
  holds. Enforced client-side (`countReferenceFiles`/`referenceSlotCost` + the live
  `n / 12` counter and a per-row `2 files`/`1 file`/`inactive`/`off` caption; the three
  edits that can change a row's price — filling it, ticking a track, switching it on —
  all go through one prospective-cost check, `refWouldExceed` in `commands.js`; a drop
  with exactly one slot free lands video-only rather than being refused) and server-side
  (`_resolve_references` in `app.py` returns **400** over `REFERENCE_MAX_FILES`, which
  now lives in `config.py` so both sides quote one number).
- **Sources**: image slots hold gallery `/images/` URLs (chat media, or desktop images
  imported via `/api/import-image`). Videos may be a gallery clip or an uploaded
  `/references-file/` URL; standalone audio is always uploaded. Desktop video/audio go to
  `POST /api/upload-reference` (multipart `file` + `kind`), stored in the dot-prefixed,
  **persistent** `REFERENCES_DIR` (`.references/` under `IMAGES_DIR`, kept out of
  galleries) and served by `GET /references-file/<name>`. Not single-use tokens — a
  reference is reused across generations.
- **Drag & drop** (`commands.js` `/references` table, `chat.js`): rows accept desktop
  files (branch on MIME) and in-app drags of chat media (a custom `application/x-comfy-url`
  dataTransfer type set by `appendChatImage`, distinct from the global file-drop overlay's
  `Files` type).
- **Tags & filling** (`generation_service.py` `_fill_reference` loop): each `<REFERENCE_*>`
  token is filled only if present in the template, uploaded via `ComfyServer.upload_media`
  (content-type by extension; ComfyUI's `/upload/image` routes video/audio too).
  `<REFERENCE_IMAGE_1>` keeps the `<INPUT_IMAGE>` fallback (mandatory); the other optional
  slots (images 2–9, all videos/audios), when unset, get a `reference_sentinel()` filename
  and their loader nodes are removed by `strip_reference_nodes()` (`workflow.py`) after
  JSON parse — the node is deleted and any consumer input pointing at it is dropped (absent
  optional input).
- **Video tracks on the wire**: `_resolve_references` resolves a video URL **once**, as a
  video, and writes the same `Path` into `input_reference_videos[i]` and
  `input_reference_video_audios[i]` per its flags. "Same Path in both" therefore means
  both tracks — and `ref_upload_cache` (keyed on the path) uploads the clip to ComfyUI
  only once. Both flags off = inactive: never resolved, never sent.
- **Two template conventions** for the tracks:
  - **single-node** (documented, what the MiniMax graph uses): one VHS loader holds
    `<REFERENCE_VIDEO_n>` and drives both an IMAGE and an AUDIO consumer input. Since the
    node must still load the clip for whichever track *is* wanted, an unticked box is
    honoured by dropping just that output's links —
    `drop_node_output_links()` (`workflow.py`), applied **after** `strip_reference_nodes`
    so an inactive slot's node is already gone. Which output index is which comes from
    `ComfyServer.get_node_output_types()` (`GET /object_info/<class_type>`, cached per
    server+class): the AUDIO-typed index, or every non-AUDIO index. Reading the declared
    types beats hardcoding indices — VideoHelperSuite's output tuple has shifted across
    releases, the type names haven't. If that lookup fails we classify by the consumer's
    input name (`node_link_output_indices`), and if that's inconclusive too the job
    **errors** rather than silently rendering the opposite of what was ticked.
    The loader node is located by `reference_marker()`, a per-token locator substituted in
    place of the filename and overwritten with the real name right after `json.loads` —
    a filename scan can't tell two slots holding the same clip apart.
  - **two-node**: the template also carries `<REFERENCE_VIDEO_AUDIO_n>` on a second loader
    pointed at the same clip. Each token then stands alone, so an unwanted track uses the
    plain sentinel + strip path and no output surgery is needed. Kept working as an escape
    hatch.
- **API format only**: all node stripping (LoRA, last-frame guide, references, track
  links) runs **before** `convert_ui_to_api_format`, so a template that needs any of it
  must be exported from ComfyUI in **API format**.
- **The workflow JSON is authored separately** (in `~/comfy-workflows/`); this feature
  only defines the tags/plumbing. Reference file names are **string** slots (quote them in
  the template).

### Video settings detail

- `<DURATION>`, `<FRAMES>` and `<FPS>` are interdependent: `frames = duration × fps`. The `/video-settings` UI keeps them consistent — you lock one value (only one at a time) and editing either of the other two re-derives the third. The math lives in `utils.js` (`clampVideo` / `recomputeVideo`) and is unit-tested.
- Output is driven by `<FRAMES>` and `<FPS>` (both integers, fed to `PrimitiveInt` nodes); `<DURATION>` is the human-facing value and may round by a frame at the extremes.
- In the LTXV image2video template, the latent length math node consumes the Frames primitive as `frames + 1` (the extra conditioning frame), the Frame Rate primitive feeds the conditioning/audio/CreateVideo nodes, and the Duration primitive is informational.
- The Wan 2.2 14B image2video template (`image2video/wan22_14B_i2v.json`) uses the same placeholders. Its `<FPS>`/`<DURATION>` feed `PrimitiveFloat` nodes (not `PrimitiveInt`) — injecting a bare integer is still valid JSON. Length is driven by a `<FRAMES>` `PrimitiveInt` (node `129:164`) through a `Math Expression (length)` node (`129:163`) as `frames + 1`, mirroring LTXV; the FPS primitive also feeds `CreateVideo`, and the Duration primitive is informational. This template has **no** audio nodes.
- **Video resolution**: `/video-settings` also sets `<VIDEO_WIDTH>`/`<VIDEO_HEIGHT>` (stored on `currentVideoSettings.width`/`.height`, default `1280×720`), sent to `/api/image2video` as `video_width`/`video_height`. This is deliberately **separate** from the still-image resolution in `/image-settings` (which flows through `apply_resolution`/`currentResolution`) because video models have very different size constraints. Dimensions are clamped to 64–2048 and snapped to a multiple of 16 (`clampVideo` in `utils.js`). In templates they replace the width/height primitives directly: the Wan templates' `WanImageToVideo` width/height (node `129:98`), and the LTX template's separate `Width`/`Height` `PrimitiveInt` nodes (`320:312`/`320:299`). The Wan and image-resolution paths don't collide because image2video never sends the still `width`/`height`, so `apply_resolution` isn't called for it.
- **Audio toggle**: `/video-settings` has an Audio checkbox stored on `currentVideoSettings.audio` (default `true`). It is purely client-side — when off, `buildVideoPrompt()` (`utils.js`) drops the `Audio: <audio>` segment that `/video-sequence` folds into a video prompt, so audio-less workflows (e.g. the Wan template) aren't fed audio cues they ignore. It does not alter the workflow graph; audio-capable workflows still generate their own audio track regardless.

### Video optimisation toggles (`/video-settings`)

The MiniMax H3 workflows carry seven speed-for-quality optimisations — **4-step turbo
LoRA**, **8-step accel LoRA (fl2va)**, **8-step accel LoRA (ref2va)**, **H3
FirstBlockCache**, **Sage attention**, **Sol attention**, **H3 Spectrum** — each a
`MODEL → MODEL` passthrough chained between the `UNETLoader` and the guider/scheduler.
They used to be baked in, which meant one template file per combination (eight of them,
differing in nothing else). They are now **one chain in one template per kind**,
bypassed per run from checkboxes in `/video-settings`.

There are consequently three H3 templates, not eight: `image2video/minimax-h3-i2v.json`,
`text2video/minimax-h3-t2v.json` and `text2video/minimax-h3-r2v.json`. They stay separate
files because they are different graphs, not optimisation variants — R2V uses a different
UNET (`minimax_h3_ref2va_…`) with the 15-slot `MiniMaxH3ReferenceToVideo` node, and I2V
adds the `LoadImage → resize → first_frame` sub-chain to T2V's graph.

- **Marked by title, not class.** A node is an optimisation iff its `_meta.title` starts
  with `[opt:<key>]`, key ∈ `turbo accel8fl accel8ref cache sage sol spectrum`
  (`OPT_TITLE_RE` / `optimisation_nodes` in `workflow.py`). Deliberately **not**
  `class_type`: the i2v graph holds *three* `LoraLoaderModelOnly` nodes — the turbo LoRA,
  the 8-step accel LoRA and the `<LORA_1_NAME>` user slot — and only the marked ones may
  be bypassed. Node titles also survive a ComfyUI re-export, so re-editing a template in
  the editor does not lose the marking.
- **Two 8-step variants, one per UNET.** `accel8fl`
  (`h3\minimax-h3-fl2va-acc-8step.safetensors`) is in the i2v and t2v templates, which
  share the `fl2va` UNET; `accel8ref` (`…-ref2va-acc-8step…`) is in r2v, which uses
  `ref2va`. Each sits directly after `[opt:turbo]` in the chain. Both checkboxes are
  always shown — the panel does not know which workflow is active, and a key absent from
  a template bypasses nothing, so ticking the irrelevant one is a harmless no-op.
- **`bypass_optimisation_nodes()`** (`workflow.py`) deletes each marked node and rewires
  its consumers to whatever fed its `model` input — the same `_rewire_references`
  passthrough `strip_lora_nodes` uses. Nodes go **one at a time**, which makes chained
  removals correct without a special case: dropping sage first repoints sol's `model` at
  sage's upstream. A marked node with no `model` input **raises** rather than leaving a
  dangling reference. Called in `_run_generation_core` alongside the other node surgery,
  after the track drops and before the UI→API conversion — so, as with all of it, the
  template must be exported in **API format**.
- **Wire format**: the client sends `video_opts` as `{key: bool}` for **every** key;
  `_parse_video_opts` (`app.py`) returns the set of keys that are **off**, forwarded as
  `disabled_optimizations`. An absent `video_opts` disables nothing, so an older client
  or a non-video job runs the template exactly as authored — which is why every flag is
  sent explicitly rather than only the off ones (`videoOptsPayload` in `utils.js`).
  Unknown keys are a **400**, checked against `VIDEO_OPTIMIZATIONS` in `config.py`
  (mirrored client-side in `utils.js`, which is where the descriptors live rather than
  `state.js` because `state.js` imports from `utils.js`).
- **Client state is flat booleans** on `currentVideoSettings` — `optTurbo`,
  `optAccel8Fl`, `optAccel8Ref`, `optCache`, `optSage`, `optSol`, `optSpectrum`, all
  read with the `!== false` idiom so an absent key means on. Flat, not a nested `opts`
  object, because
  both restore paths shallow-merge (`{ ...DEFAULT_VIDEO_SETTINGS, ...s.videoSettings }`)
  and Apply does `{ ...work }`: a nested object would be replaced wholesale by an old
  snapshot and would share a reference between the panel and state. Flat keys ride the
  existing spread, so `saveSession`/`restoreSession`, the `/settings-save` stack and
  `newChat` needed **no** changes — exactly as when `audio` was added.
- **An accelerator carries its step count.** A descriptor with a `steps` field
  (`VIDEO_OPTIMIZATIONS` in `utils.js`) is an accelerator — a distillation LoRA that only
  makes sense at that step count. The templates bake `steps: 20`; ticking one switches
  the steps override on and sets it to that LoRA's count (4 or 8), unticking the last one
  re-ticks *Use workflow default* — one-way coupling only, so an explicit steps edit
  afterwards still wins. This lives purely in the panel: forcing steps at send time would
  also hit any template with no accelerator node to untick.
- **Accelerators are mutually exclusive by step count.** A 4-step and an 8-step
  distillation LoRA chained together give mush, so ticking one unticks every accelerator
  of a *different* count (and updates its checkbox). The two 8-step variants share a
  count and so do **not** exclude each other — they are never both present in one
  template anyway, so "both on" just means "whichever 8-step LoRA this workflow has".
- **`activeAccelerator(vs)`** (`utils.js`) is the single resolver: the on accelerator
  with the **lowest** step count wins. Several can read as on only in a session saved
  before the 8-step LoRAs existed — it carries an explicit `optTurbo: true` and no 8-step
  keys, and absent reads as on — so lowest-wins resolves such a session back to the turbo
  LoRA it actually chose, and it renders exactly as before. `videoOptsPayload` forces the
  losing group to `false` on the wire, the panel collapses `work` the same way on open,
  and the `/settings` summary reads through the payload so all three agree.
- **Default is the 8-step accel plus the four non-LoRA optimisations**; turbo is off.
  ⚠ That stack has **never been rendered** — Spectrum previously appeared only in a
  32-step HQ t2v file where it *replaced* FirstBlockCache, and its forecast params
  (warmup 1, window 2, max_history 8) have little to work with across 8 steps.
  Test-render the default stack in the ComfyUI editor before trusting it.
- **Known gap**: on a fresh session where `/video-settings` has never been opened,
  `currentVideoSteps` is `null` while an accelerator is on, so the first run loads that
  LoRA at the template's 20 steps — slower than intended, not broken. Opening the panel
  once (it pre-fills the override to the LoRA's count) and pressing Apply fixes it for
  the session.

### Alternate models in a workflow (`UNETLoader` comma lists)

Templates used to be duplicated whenever the only difference was the diffusion model
file — an int8 build and an fp16 build of the same graph. A template now declares its
alternates **inline**, as a comma-separated model name, and `/workflows` drills down one
more level (type → workflow → model) to pick one:

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

- **`UNETLoader.unet_name` only**, via `MODEL_VARIANT_INPUTS` (`workflow.py`) — a
  `{class_type: input}` dict so a later `CheckpointLoaderSimple`/`VAELoader` is one line.
  Unlike the `[opt:…]` markers this *is* keyed on `class_type`, because there is no
  ambiguity to resolve: only a model loader has a model slot, and a node holding a single
  name is simply not a choice.
- **The pick rides the workflow name** as `<workflow>@<model>`
  (`WORKFLOW_VARIANT_SEP`), where `<model>` is the filename without its extension. That
  is the whole reason it needed no plumbing: every payload, `saveSession`/`restoreSession`,
  the `/settings-save` stack, `newChat`, macros and the server-side `/api/sequence-run`
  already carry a workflow name, so they carry the model too. The cost is that the suffix
  shows in the header badge and `/jobs` summaries. `splitWorkflowVariant` (`utils.js`) and
  `split_workflow_variant` (`workflow.py`) split on the **last** `@`; a workflow whose own
  filename contains one still resolves, because the suffixed reading is only preferred
  when that base file actually exists (`resolve_workflow_path`, and the exact-match-first
  check in `resolve_workflow`).
- **Several multi-valued loaders are index-paired.** Wan 2.2 carries a high-noise and a
  low-noise `UNETLoader`; if both declare alternates they must declare the **same
  number**, and variant *n* takes the n-th entry from each — one pick, a matched pair of
  builds. A count mismatch **raises** rather than pairing the wrong files. Labels come
  from the first node (sorted by node id, so they are stable).
- **`select_model_variant()` always runs** (`_run_generation_core`, right after the
  UI→API conversion), with the first alternate as the default — so a comma list can never
  reach ComfyUI. It sits *after* the conversion, unlike the rest of the node surgery,
  because it only rewrites an input string: nothing depends on the ordering, and running
  it there covers UI-format templates too. An `@model` that the template doesn't offer
  **fails the job** naming what it does offer, rather than quietly rendering a different
  model — a stale pick is fixed by re-picking in `/workflows`.
- **Listing needs API format.** `GET /api/workflow-variants/<kind>/<name>`
  (`list_workflow_variants`) reads the template, runs the existing
  `fill_placeholders_for_validation()` over it (a raw template isn't valid JSON — it still
  holds `<PROMPT>`) and parses it. A UI-format export carries no `class_type`, so it
  reports **no** alternates even though generation would collapse them. Every failure —
  missing file, unparseable template, mismatched lists — answers `[]`, so the picker just
  shows no extra level. `kind` indexes `config.WORKFLOW_KIND_DIRS`; unknown → 404.
- **Fetched lazily, per workflow clicked**, not folded into the eight listing endpoints,
  which would mean parsing every template on every picker open. Cached per row in the
  `/workflows` table. Both picker surfaces have the level: the table and the shared
  `renderWorkflowPicker` behind the eight `/<x>-workflow` commands, so a per-type command
  can't silently drop a model the table set. The first alternate is captioned
  `(default)` and stores the **bare** name, keeping the wire format byte-identical to
  before for anyone who never touches it.
- **Not covered:** `/t2i-workflow-iterate` passes bare names, so each iterated workflow
  runs its default model. A model filename containing a comma can't be used.

### Text-to-video (`/t2v`)

`/t2v` is a **mode toggle**: while it is on, a plain chat prompt is generated as a video
by the text2video workflow instead of an image by the t2i one. Typing `/t2v` again turns
it off. The header shows the active t2v workflow plus a `🎬 t2v` badge so the mode is
never invisible.

- **Its own workflow family**, mirroring the other seven: `COMFY_TEXT2VIDEO_DIR`
  (`text2video/` under `COMFY_WORKFLOW_DIR`) + `COMFY_TEXT2VIDEO_WORKFLOW`
  (`config.py`), `list_text2video_workflows()` (`catalogue.py`),
  `/api/text2video-workflows`, and the `/t2v-workflow` / `/t2v-workflow-reset` picker.
  It deliberately does **not** reuse the `/i2v-workflow` selection — an i2v graph and a
  t2v graph are different graphs, and keeping them in separate dirs means no node
  stripping or rewiring is needed at generation time.
- **Placeholder set**: `<PROMPT>`, `<DURATION>`, `<FRAMES>`, `<FPS>`, `<VIDEO_WIDTH>`,
  `<VIDEO_HEIGHT>`, and optionally the `<REFERENCE_*>` slots. A t2v template must have
  **no** `<INPUT_IMAGE>` — with no source image the mapping key is never set, and the
  unfilled-placeholder check in `_run_generation_core` would fail the job with
  `Unfilled workflow placeholders: <INPUT_IMAGE>`.
- **`<REFERENCE_IMAGE_1>`** still works (set one via `/references`) for identity-
  preserving models such as the LTX 2.3 `ref_t2v` graph. There is no first frame to fall
  back on here, so `_run_generation_core` raises a clear "needs a `<REFERENCE_IMAGE_1>`"
  error instead of substituting an empty `LoadImage` name.
- **`/api/text2video`** (`app.py`) is `/api/image2video` minus the image plumbing: no
  `image`, no `last_frame`, prompt required, `workflow_dir=COMFY_TEXT2VIDEO_DIR`. LoRA
  tags are parsed and discarded, as for i2v. `start_generation_job` classifies the job as
  `kind: "video"` off the video kwargs, so `/jobs` labels it correctly with no extra work.
- **Scope**: the mode intercepts the plain-prompt path in `sendMessage` and plain `#macro`
  steps (`chat.js`). It does **not** affect `/sequence-run` (its loop is server-side, in
  `/api/sequence-run`) or `/multi-prompt`. `state.t2vMode` persists with the chat session
  and the `/settings-save` stack, and resets in `newChat`.
- **Deriving a t2v template from an i2v one** (e.g. MiniMax H3): delete the `LoadImage`
  node holding `<INPUT_IMAGE>` and any resize node feeding off it, then delete the video
  node's now-dangling first-frame input key (`first_frame` on `MiniMaxH3ImageToVideo`).
  Test-render in the ComfyUI editor first — if that input turns out to be **required**,
  use the node pack's dedicated text-to-video class instead.

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

### Frozen backing-file mtime

The agent keeps each volume's **backing file** mtime pinned to a fixed baseline so that
routine housekeeping (mount/unmount, `e2fsck`, LUKS keyslot edits on password change,
the marker/chmod/superblock writes) never advances it. `_freeze_mtime()` in
`packaging/agent/archive-agent` `os.utime()`s the file back to a per-volume baseline
after every op that leaves the volume closed (`handle_unmount`/`host-unmount`,
`handle_fsck`, `handle_add_key`/`remove_key`). The baseline is a tiny 0600 sidecar under
`MTIME_BASELINE_DIR` (`/var/lib/archive-agent/mtime-baselines`), written **once** at
volume creation (or bootstrapped from the current mtime the first time an existing volume
is touched) and **never changed** thereafter — so even archiving new images doesn't move
the mtime (`api_archive`'s mount→copy→unmount ends in `handle_unmount`). Toggle with the
`PRESERVE_MTIME` config key (default `"1"`); reset by deleting the volume's sidecar.

- **Archive volume** (unmounted at rest): fully frozen — every observable at-rest moment
  shows the baseline date.
- **Output volume** (mounted continuously): frozen only when **closed** (container stop,
  rekey/lazy-mount cycle); its mtime drifts while live-mounted and cannot be pinned
  without freezing the running filesystem.
- Only the *outer* host file's mtime is changed, never the *inner* ext4 superblock
  timestamps `e2fsck` uses — fs integrity and cryptsetup/zuluCrypt open are unaffected.
  `ctime` still moves (a `utime` side effect); this only matters to backup tools that key
  on `ctime` rather than mtime+size.

**Backup implication:** the LUKS file is a **fixed size**, so with mtime frozen a
size+mtime backup (plain `rsync`, tar `--newer-mtime`) will never re-copy it — use a
content-aware backup (`rsync -c`, `restic`, `borg`) if the archive must be backed up.

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
- **ComfyUI workflows**: `~/comfy-workflows/` on the host `$PROD_SERVER` (bind-mounted into the container at `/app/workflows` per that compose file; image2video templates live in `~/comfy-workflows/image2video/`)

## Releasing & deploying (the `push-to-portainer` skill)

After **successfully completing a feature** (change implemented, tests/build passing),
run the **`push-to-portainer`** skill to release and deploy it: it commits & pushes to
`main`, cuts a release with `scripts/make-release`, watches the GitHub Actions build
(fixing any failures), and then redeploys the live `comfy-chatbot` stack on the Portainer
server ($PROD_SERVER) to pull the new image.

**Always get explicit confirmation from the user before the Portainer update (Stage 4).**
The redeploy restarts the live service, so pause after the release build is green and ask
the user to approve before running the redeploy — never update Portainer automatically.
