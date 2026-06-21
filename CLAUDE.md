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

### Numeric placeholders (replaced as bare JSON numbers, not quoted strings)

| Placeholder | Description |
|---|---|
| `<LORA_1_STRENGTH>` | Strength of the first LoRA (float, e.g. `0.8`), sourced from the `<lora:name:strength>` tag; defaults to `1.0` if omitted |
| `<DENOISE>` | Denoising strength for KSampler nodes (float 0.0–1.0); used in img2img workflows |
| `<DURATION>` | Video duration in seconds (float); image2video workflows. Set via `/video-settings` |
| `<FRAMES>` | Video frame count (int); image2video workflows. Set via `/video-settings` |
| `<FPS>` | Video frames per second (int); image2video workflows. Set via `/video-settings` |

### LoRA handling detail

- Multiple LoRAs are supported: `<LORA_1_NAME>` / `<LORA_1_STRENGTH>`, `<LORA_2_NAME>` / `<LORA_2_STRENGTH>`, etc.
- LoRA slots with no corresponding `<lora:...>` tag in the prompt are filled with a sentinel value and then the entire LoRA node is removed from the workflow graph, with its model/clip outputs rewired to bypass it (`strip_lora_nodes()` in `workflow.py`).
- The pattern for matching lora tags in user input is `<lora:name:strength>` (case-insensitive); strength is optional and defaults to `1.0`.

### First-frame / last-frame detail (image2video)

- The LTX 2.3 image2video template optionally accepts a second image, `<INPUT_LAST_FRAME>`, so the model interpolates from the source (first) frame to a designated end frame, instead of only conditioning on the first frame.
- The end frame is conditioned by an **`LTXVAddGuide`** node (node `320:330`) pinned to the final frame (`frame_idx = -1`). LTX's `LTXVImgToVideoInplace` (used for the first frame) has **no** frame index, so it cannot place a last frame — `LTXVAddGuide` is required. The graph also contains the paired `LTXVCropGuides` (`320:284`) that strips the guide frames back out after sampling.
- The guide is toggled by a float placeholder, `<LAST_FRAME_STRENGTH>` (a bare JSON number fed to a `PrimitiveFloat` node, `320:325`): `1.0` = on, `0.0` = off. The UI designates the end frame with the 🎞️ button on an image (`makeLastFrameButton` / global `lastFrameUrl` in `chat.js`); `/api/image2video` accepts a `last_frame` image URL.
- When **no** end frame is supplied, `run_generation` fills `<INPUT_LAST_FRAME>` with the same uploaded name as `<INPUT_IMAGE>` and sets `<LAST_FRAME_STRENGTH>` to `0.0`, so the guide contributes nothing and the workflow behaves exactly like the original single-image image2video. When one **is** supplied, strength is `1.0` and the second image drives the end frame.
- Wiring: `Load Last Frame` (`270`) → `Resize Last Frame` (`320:331`) → `LTXVPreprocess` (`320:332`) → `LTXVAddGuide` (`320:330`). The guide takes its conditioning from `LTXVConditioning` (`320:304`) and its latent from the first-frame `LTXVImgToVideoInplace` (`320:296`); its outputs feed the pass-1 concat (`320:318`), pass-1 guider (`320:314`) and `LTXVCropGuides` (`320:284`).

### Video settings detail

- `<DURATION>`, `<FRAMES>` and `<FPS>` are interdependent: `frames = duration × fps`. The `/video-settings` UI keeps them consistent — you lock one value (only one at a time) and editing either of the other two re-derives the third. The math lives in `utils.js` (`clampVideo` / `recomputeVideo`) and is unit-tested.
- Output is driven by `<FRAMES>` and `<FPS>` (both integers, fed to `PrimitiveInt` nodes); `<DURATION>` is the human-facing value and may round by a frame at the extremes.
- In the LTXV image2video template, the latent length math node consumes the Frames primitive as `frames + 1` (the extra conditioning frame), the Frame Rate primitive feeds the conditioning/audio/CreateVideo nodes, and the Duration primitive is informational.
- The Wan 2.2 14B image2video template (`image2video/wan22_14B_i2v.json`) uses the same placeholders. Its `<FPS>`/`<DURATION>` feed `PrimitiveFloat` nodes (not `PrimitiveInt`) — injecting a bare integer is still valid JSON. Length is driven by a `<FRAMES>` `PrimitiveInt` (node `129:164`) through a `Math Expression (length)` node (`129:163`) as `frames + 1`, mirroring LTXV; the FPS primitive also feeds `CreateVideo`, and the Duration primitive is informational. This template has **no** audio nodes.
- **Audio toggle**: `/video-settings` has an Audio checkbox stored on `currentVideoSettings.audio` (default `true`). It is purely client-side — when off, `buildVideoPrompt()` (`utils.js`) drops the `Audio: <audio>` segment that `/video-sequence` folds into a video prompt, so audio-less workflows (e.g. the Wan template) aren't fed audio cues they ignore. It does not alter the workflow graph; audio-capable workflows still generate their own audio track regardless.

### Validation

`fill_placeholders_for_validation()` substitutes dummy values (`1.0` for float slots including `<LAST_FRAME_STRENGTH>`, `1` for the integer video slots, `"placeholder"` for string slots) so a template file can be parsed as valid JSON during startup validation.

## Live Configuration (Host Machine)

The `docker-compose.yml` in this repo is an **example only**. The live deployment uses:

- **Docker Compose file**: `~/dot-files/docker-compose/comfy-chatbot.yml`
- **ComfyUI workflows**: `~/dot-files/comfyui/` (bind-mounted into the container at `/app/workflows`)