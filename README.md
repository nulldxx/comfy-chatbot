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

```bash
# 1. Edit docker-compose.yml — set COMFY_SERVER to your ComfyUI address
# 2. Build and run
docker-compose up --build -d

# 3. Open http://localhost:5000  (default login: user / password)
```

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

## Workflow Templates

Templates are ComfyUI API-format JSON files with placeholder tokens:

- `<PROMPT>` — the user's prompt text
- `<LORA_1_NAME>`, `<LORA_2_NAME>`, … — LoRA file paths
- `<LORA_1_STRENGTH>`, `<LORA_2_STRENGTH>`, … — LoRA strengths (numeric, unquoted)

Unused LoRA slots are stripped from the graph automatically.

## Local Development

```bash
pip install -r requirements.txt

COMFY_SERVER=myserver:8000 \
COMFY_SERVER_OS=unix \
COMFY_WORKFLOW=my_workflow \
COMFY_WORKFLOW_DIR=~/dot-files/comfyui \
COMFY_LORAS_FILE=~/dot-files/comfyui/loras.json \
COMFY_OUTPUT_DIR=~/Pictures/ComfyUI \
python app.py
```

## Project Structure

```
app.py               # Flask app — routes, generation thread, SSE
ComfyServer.py       # ComfyUI HTTP client (submit, poll, download)
templates/
  index.html         # Chat UI
  login.html         # Login page
docker-compose.yml
Dockerfile
gunicorn.conf.py     # gthread workers for SSE support
```

## Release / CI

Push a version tag to trigger a Docker Hub build:

```bash
git tag v1.0.0 && git push origin v1.0.0
```

Required GitHub secrets: `DOCKER_USERNAME`, `DOCKER_PASSWORD` (Docker Hub access token).
