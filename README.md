# ComfyUI Chat

A self-hosted web chat interface for generating images with [ComfyUI](https://github.com/comfyanonymous/ComfyUI). Type a prompt, watch progress stream in real time, and see the generated image appear inline in the conversation.

## Screenshot

<img width="1412" height="1613" alt="image" src="https://github.com/user-attachments/assets/2227c42b-5471-41f7-85a5-7388e5a89697" />

## Features

- **Chat UI** — prompt history (↑/↓), LoRA chip shortcuts, lightbox image viewer
- **LoRA tags** — include `<lora:name:strength>` anywhere in a prompt
- **Live progress** — Server-Sent Events stream status updates while ComfyUI runs
- **Slash commands** — `/help`, `/server`, `/addserver`, `/t2i-workflow`
- **Multi-server** — switch between ComfyUI instances at runtime; supports both Unix and Windows path conventions
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
| `COMFY_FACEDETAILER_WORKFLOW` | *(first found)* | Default face-detailer workflow (in `facedetailer/` subdir) used by `/face-detail` |
| `COMFY_WORKFLOW_DIR` | `/app/workflows` | Directory of workflow `.json` templates |
| `COMFY_LORAS_FILE` | `/app/workflows/loras.json` | LoRA catalogue |
| `COMFY_OUTPUT_DIR` | `/app/output` | Where generated images are saved |
| `APP_USERNAME` | `user` | Login username |
| `APP_PASSWORD` | `password` | Login password |
| `SECRET_KEY` | *(change this)* | Flask session secret |
| `ARCHIVE_VOLUME` | *(empty)* | Host path to the encrypted volume for `/archive-*`, encrypted with `SECRET_KEY` and auto-created on first archive (blank disables archiving) |
| `ARCHIVE_SIZE` | `20G` | Size of the archive volume created on first archive (zuluCrypt-cli units) |
| `ARCHIVE_AGENT_SOCKET` | `/run/archive-agent.sock` | Unix socket of the host archive agent |
| `ARCHIVE_MOUNT_DIR` | `/app/archive` | Where the host mount appears inside the container |
| `OUTPUT_VOLUME` | *(empty)* | Host path to the encrypted **live-output** volume (blank = plain, unencrypted output). Auto-created on first deploy |
| `OUTPUT_PASSWORD` | *(empty)* | Passphrase for the output volume (falls back to `SECRET_KEY`, so you can declare one secret) |
| `OUTPUT_SIZE` | `20G` | Size of the output volume created on first deploy (zuluCrypt-cli units) |

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
| `/t2i-workflow` | Pick a workflow template |
| `/face-detail <prompt>` | Run a face-detailer workflow over the last generated image (supports `<lora:…>` tags) |
| `/face-detail-workflow` | Pick which face-detailer workflow `/face-detail` uses (from the `facedetailer/` subdir) |
| `/archive-session [name]` | Copy this session's images into the encrypted volume, then delete the originals |
| `/archive-today [name]` | Archive images generated today into the encrypted volume |
| `/archive-all [name]` | Archive every image in the output folder (asks y/n first) |

## Encrypted Archiving

The `/archive-*` commands copy images into an encrypted volume (opened with
[zuluCrypt](https://github.com/mhogomchungu/zuluCrypt)) and then delete the
originals from the output folder — a *move*, not a backup copy. The volume is
encrypted with `SECRET_KEY` (the deployment's single secret) and **self-provisions
on first use**: if the volume file doesn't exist yet, the agent creates it.

Each command stages its images under `staging/<folder>` on the volume. By
default `<folder>` is a random GUID, but you can supply a name to use instead —
e.g. `/archive-session man walking on beach` stages into
`staging/man-walking-on-beach`. Names are lower-cased and hyphenated; if the name
contains nothing usable, a GUID is used.

Because the container runs unprivileged, it cannot mount the volume itself.
Instead it asks a small **host-side root agent** (`archive-agent`) to run
`zuluCrypt-cli` over a Unix socket. The container sends the volume path and the
`SECRET_KEY` passphrase **per request**; the agent never stores them.

**1. Install the agent on the host** (e.g. Raspberry Pi 5 / Debian Bookworm). The
`.deb` is attached to each [GitHub Release](../../releases) (it's
`Architecture: all`, so one package works on arm64 and amd64):

```bash
sudo apt install ./archive-agent_<version>_all.deb
```

This pulls in `zulucrypt-cli`, installs an `archive-agent` systemd service,
and sets up the mount directory `/var/lib/archive-agent/mnt` as a **shared mount**
so the mount propagates into the container.

**2. Point the container at it** via the `ARCHIVE_*` variables above and the bind
mounts already present in `docker-compose.yml`:

```yaml
environment:
  - ARCHIVE_VOLUME=/srv/archives/photos.luks   # host path the agent opens; auto-created on first archive
  - ARCHIVE_SIZE=20G                            # size of the volume if auto-created
volumes:
  - /run/archive-agent.sock:/run/archive-agent.sock
  - type: bind
    source: /var/lib/comfy-archive/mnt        # = agent MOUNT_DIR (shared mount)
    target: /app/archive
    bind:
      propagation: rshared                    # required: makes the mount visible
```

zuluCrypt auto-detects LUKS/VeraCrypt, so the volume can be either. Tune the agent
(socket path/permissions, mount dir) in `/etc/archive-agent.conf`.

## Encrypting the live output folder

`/archive-*` secures images you've finished with, but freshly generated images
still land in the **plain** output folder (`~/Pictures/ComfyUI` by default), where
anyone with host or disk access can browse them. Setting `OUTPUT_VOLUME` encrypts
that live output too, reusing the same `archive-agent`.

When `OUTPUT_VOLUME` is set, the container entrypoint asks the agent (on start) to
**create the LUKS volume if it doesn't exist yet** — so it self-provisions on first
deploy — then mounts it at `/app/output`, and **unmounts it when the container
stops**. Generated images are therefore encrypted at rest whenever the chatbot
isn't running. The app refuses to generate if the volume didn't mount, so it never
silently writes plaintext images to disk.

**To enable**, install the same `archive-agent` (above), then in `docker-compose.yml`:

```yaml
environment:
  - OUTPUT_VOLUME=/srv/comfy/live-output.luks   # auto-created on first deploy
  - OUTPUT_PASSWORD=change-me                    # omit to reuse SECRET_KEY
  - OUTPUT_SIZE=20G
volumes:
  # Replace the plain `~/Pictures/ComfyUI:/app/output` line with the encrypted bind:
  - type: bind
    source: /var/lib/archive-agent/output        # = agent OUTPUT_MOUNT_DIR (shared mount)
    target: /app/output
    bind:
      propagation: rshared
```

**Threat model.** This protects images **at rest** — when the container is stopped,
the host is powered off, or the disk is removed, `OUTPUT_VOLUME` is just an opaque
LUKS blob. It does **not** protect against a compromised running host (the volume is
mounted and readable while the app runs) and the passphrase lives in the container
environment. Same trade-off as `/archive-*`.

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
| A `LoadImage` filename | `<INPUT_IMAGE>` | For `facedetailer/` workflows — filled with the last generated image, which `/face-detail` uploads to the server |

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

