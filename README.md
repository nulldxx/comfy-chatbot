# ComfyUI Chat

A self-hosted web chat interface for generating images with [ComfyUI](https://github.com/comfyanonymous/ComfyUI). Type a prompt, watch progress stream in real time, and see the generated image appear inline in the conversation.

## Screenshot

<img width="1412" height="1613" alt="image" src="https://github.com/user-attachments/assets/2227c42b-5471-41f7-85a5-7388e5a89697" />

## Features

- **Chat UI** — prompt history (↑/↓), LoRA chip shortcuts, lightbox image viewer
- **LoRA tags** — include `<lora:name:strength>` anywhere in a prompt
- **Live progress** — Server-Sent Events stream status updates while ComfyUI runs
- **Slash commands** — `/help`, `/server`, `/addserver`, `/workflow`, `/upload`
- **Multi-server** — switch between ComfyUI instances at runtime; supports both Unix and Windows path conventions
- **Workflow upload** — upload new workflow templates directly from the chat UI
- **Persistent output** — generated images saved to a configurable host directory

## Quick Start

The easiest way to run ComfyUI Chat is with the published image on Docker Hub ([nerwander/comfy-chatbot](https://hub.docker.com/r/nerwander/comfy-chatbot)).

1. **Download [`docker-compose.yml`](docker-compose.yml)** from this repo
2. **Edit it** — set `COMFY_SERVER` to your ComfyUI address and adjust the volume mounts
3. **Run it**

```bash
docker-compose up -d
```

4. Open **http://localhost:5000** — default login: `user` / `password`

To build from source instead, replace `image: nerwander/comfy-chatbot:latest` with `build: .` (or keep both — Docker Compose uses `build` when present and falls back to `image` for `docker pull`).

## Configuration

All settings are environment variables in `docker-compose.yml`:

| Variable | Default | Description |
|---|---|---|
| `COMFY_SERVER` | `192.168.1.135:8000` | ComfyUI server address (`host:port`) |
| `COMFY_SERVER_OS` | `unix` | Path style sent to server: `unix` or `windows` |
| `COMFY_WORKFLOW` | `z_image_turbo_api` | Default workflow template name |
| `COMFY_WORKFLOW_DIR` | `/app/workflows` | Directory of workflow `.json` templates |
| `COMFY_LORAS_FILE` | `/app/workflows/loras.json` | LoRA catalogue |
| `COMFY_OUTPUT_DIR` | `/app/output` | Where generated images are saved |
| `APP_USERNAME` | `user` | Login username |
| `APP_PASSWORD` | `password` | Login password |
| `SECRET_KEY` | *(change this)* | Flask session secret |

### loras.json

The LoRA catalogue controls which chips appear in the chat UI and provides default strengths. Each entry can be a plain filename string or an object with an explicit strength:

```json
{
  "loras": [
    { "name": "styles/cinematic.safetensors",   "strength": 0.8 },
    { "name": "styles/anime-flat.safetensors",  "strength": 0.7 },
    { "name": "characters/hero.safetensors",    "strength": 1.0 },
    "detail-enhancer.safetensors"
  ]
}
```

Plain strings default to strength `1.0`. The `name` value is the path sent to the ComfyUI server — on Windows servers use backslashes (`styles\\cinematic.safetensors`) or set `COMFY_SERVER_OS=windows` and let the app convert them automatically.

### Volume mounts (docker-compose.yml)

```yaml
volumes:
  - ~/dot-files/comfyui:/app/workflows:ro   # workflow templates + loras.json
  - ~/Pictures/ComfyUI:/app/output           # generated image output
```

## Slash Commands

| Command | Description |
|---|---|
| `/help` | List available commands |
| `/server` | Pick a ComfyUI server from the catalogue |
| `/addserver <name> <host:port:os>` | Add a server (`os`: `unix` or `windows`) |
| `/workflow` | Pick a workflow template |
| `/upload` | Upload a new workflow `.json` file |

## Adding Workflows

### 1. Export from ComfyUI as API format

Open your workflow in ComfyUI, then in the menu enable **"Dev Mode Options"** (Settings → Enable Dev Mode Options). A new **"Save (API Format)"** button will appear. Use this — not the regular Save — to export the file. The regular format includes UI layout data that the chatbot cannot execute directly.

### 2. Add placeholder tokens

The exported JSON is a static snapshot; you need to replace the values you want the chatbot to substitute at generation time with placeholder tokens. Open the file in a text editor and replace:

| What to replace | Token to use | Notes |
|---|---|---|
| The prompt string | `<PROMPT>` | Inside the existing quotes in the JSON |
| A LoRA filename | `<LORA_1_NAME>` | Use `<LORA_2_NAME>` for a second LoRA, etc. |
| A LoRA strength | `<LORA_1_STRENGTH>` | **Remove the surrounding quotes** — this must be a bare number in the JSON |

For example, a KSampler node's prompt input might look like this before and after:

```json
// Before
"text": "a photo of a cat"

// After
"text": "<PROMPT>"
```

And a LoRA loader node:

```json
// Before
"lora_name": "my-lora.safetensors",
"strength_model": 0.8,

// After
"lora_name": "<LORA_1_NAME>",
"strength_model": <LORA_1_STRENGTH>,
```

LoRA placeholders are optional — any slots not filled by the user's prompt are automatically removed from the workflow graph before submission.

