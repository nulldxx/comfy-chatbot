import os
import subprocess
from pathlib import Path

def _resolve_build_version():
    v = os.environ.get('BUILD_VERSION', '')
    if v:
        return v
    try:
        result = subprocess.run(
            ['git', 'describe', '--tags', '--always', '--dirty'],
            capture_output=True, text=True, timeout=5,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return 'unknown'

BUILD_VERSION = _resolve_build_version()
USERNAME = os.environ.get('APP_USERNAME', 'user')
PASSWORD = os.environ.get('APP_PASSWORD', 'password')
SECRET_KEY = os.environ.get('SECRET_KEY', 'your-secret-key-change-this-in-production')

COMFY_SERVER = os.environ.get('COMFY_SERVER', '192.168.1.135:8000')
COMFY_SERVER_OS = os.environ.get('COMFY_SERVER_OS', 'unix')
# Read ComfyUI's WebSocket progress feed (ws://<COMFY_SERVER>/ws) so the UI can show
# a determinate progress bar. Set to '0' where a proxy in front of ComfyUI blocks
# WebSocket upgrades: generation is unaffected, the bar just stays indeterminate.
COMFY_WS_PROGRESS = os.environ.get('COMFY_WS_PROGRESS', '1') not in ('0', 'false', 'False', '')
COMFY_WORKFLOW = os.environ.get('COMFY_WORKFLOW', 'z_image_turbo_api')
COMFY_WORKFLOW_DIR = Path(os.environ.get('COMFY_WORKFLOW_DIR', '/app/workflows'))
COMFY_LORAS_FILE = Path(os.environ.get('COMFY_LORAS_FILE', '/app/workflows/loras-new.json'))
# Generation workflows live in a subdir of the main workflow folder, alongside
# the facedetailer/ and upscaler/ subdirs. (loras.json and servers.json stay in
# the workflow folder root.)
COMFY_GENERATION_DIR = COMFY_WORKFLOW_DIR / 'generation'


def _norm_workflow_default(raw):
    """Normalise a workflow env-default to the same relative, '/'-joined, no-.json
    form returned by list_workflow_names() — so a nested default like
    'flux/zit-face-detailer(.json)' matches a listed name."""
    if not raw:
        return None
    raw = raw.replace("\\", "/")
    return raw[:-5] if raw.endswith(".json") else raw


# Face-detailer workflows live in a subdir of the main workflow folder. They take
# the last generated image as input (via an <INPUT_IMAGE> LoadImage placeholder).
COMFY_FACEDETAILER_DIR = COMFY_WORKFLOW_DIR / 'facedetailer'
# Default face-detailer workflow. Accepts a bare name ("zit-face-detailer") or a
# nested one like "flux/zit-face-detailer(.json)"; normalised to match the names
# returned by list_facedetailer_workflows().
COMFY_FACEDETAILER_WORKFLOW = _norm_workflow_default(os.environ.get('COMFY_FACEDETAILER_WORKFLOW'))

# Upscaler workflows live in a subdir of the main workflow folder. Like the
# face-detailer ones they take the last generated image as input (via an
# <INPUT_IMAGE> LoadImage placeholder), but they take no prompt or LoRA tags.
COMFY_UPSCALER_DIR = COMFY_WORKFLOW_DIR / 'upscaler'
# Default upscaler workflow. Accepts a bare name ("zip-2k-upscale") or a nested
# one like "flux/zip-2k-upscale(.json)"; normalised to match the names returned
# by list_upscaler_workflows().
COMFY_UPSCALER_WORKFLOW = _norm_workflow_default(os.environ.get('COMFY_UPSCALER_WORKFLOW'))

# Image2image workflows live in a subdir of the main workflow folder. Like the
# face-detailer ones they take the last generated image as input (via an
# <INPUT_IMAGE> LoadImage placeholder) and support the usual <PROMPT> and
# <lora:...> tags — re-running a generation-style workflow over a prior image.
COMFY_IMAGE2IMAGE_DIR = COMFY_WORKFLOW_DIR / 'image2image'
# Default image2image workflow. Accepts a bare name ("zit-i2i") or a nested one
# like "flux/zit-i2i(.json)"; normalised to match the names returned by
# list_image2image_workflows().
COMFY_IMAGE2IMAGE_WORKFLOW = _norm_workflow_default(os.environ.get('COMFY_IMAGE2IMAGE_WORKFLOW'))

# Inpainting workflows live in a subdir of the main workflow folder. They take
# an image and a mask (via <INPUT_IMAGE> and <INPUT_MASK> placeholders) and
# the usual <PROMPT> / <lora:...> tags to inpaint the masked area.
COMFY_INPAINTING_DIR = COMFY_WORKFLOW_DIR / 'inpainting'
# Default inpainting workflow, normalised to match list_inpainting_workflows().
COMFY_INPAINTING_WORKFLOW = _norm_workflow_default(os.environ.get('COMFY_INPAINTING_WORKFLOW'))

# Object-removal workflows live in a subdir of the main workflow folder. Like
# inpainting they take <INPUT_IMAGE> and <INPUT_MASK>, but no <PROMPT> — removal
# models (e.g. LaMa) fill in background without a text prompt.
COMFY_REMOVAL_DIR = COMFY_WORKFLOW_DIR / 'removal'
# Default removal workflow, normalised to match list_removal_workflows().
COMFY_REMOVAL_WORKFLOW = _norm_workflow_default(os.environ.get('COMFY_REMOVAL_WORKFLOW'))

# Image2video workflows live in a subdir of the main workflow folder. They take
# the last generated image as input (via an <INPUT_IMAGE> LoadImage placeholder)
# and an optional <PROMPT> to guide the video generation. No LoRA or denoise
# support — those are handled in a future iteration.
COMFY_IMAGE2VIDEO_DIR = COMFY_WORKFLOW_DIR / 'image2video'
# Default image2video workflow, normalised to match list_image2video_workflows().
COMFY_IMAGE2VIDEO_WORKFLOW = _norm_workflow_default(os.environ.get('COMFY_IMAGE2VIDEO_WORKFLOW'))

# Text2video workflows live in a subdir of the main workflow folder. Unlike the
# image2video ones they take NO <INPUT_IMAGE> — the video comes from <PROMPT>
# alone, plus the usual <DURATION>/<FRAMES>/<FPS>/<VIDEO_WIDTH>/<VIDEO_HEIGHT>
# slots. Used by /t2v, which routes plain chat prompts here instead of to the
# text-to-image generation/ workflow.
COMFY_TEXT2VIDEO_DIR = COMFY_WORKFLOW_DIR / 'text2video'
# Default text2video workflow, normalised to match list_text2video_workflows().
COMFY_TEXT2VIDEO_WORKFLOW = _norm_workflow_default(os.environ.get('COMFY_TEXT2VIDEO_WORKFLOW'))

# Workflow directories by "kind" — the operation a workflow family serves. Keyed by the
# same names the /api/<kind>-workflows listing endpoints use, so one generic endpoint
# (/api/workflow-variants/<kind>/<name>) can reach any family without eight more routes.
WORKFLOW_KIND_DIRS = {
    "generation": COMFY_GENERATION_DIR,
    "facedetailer": COMFY_FACEDETAILER_DIR,
    "upscaler": COMFY_UPSCALER_DIR,
    "image2image": COMFY_IMAGE2IMAGE_DIR,
    "inpainting": COMFY_INPAINTING_DIR,
    "image2video": COMFY_IMAGE2VIDEO_DIR,
    "text2video": COMFY_TEXT2VIDEO_DIR,
    "removal": COMFY_REMOVAL_DIR,
}


def _best_effort_mkdir(p):
    """mkdir -p that never raises. These dirs live on the encrypted output volume,
    which — once a login password is set — is deferred and NOT mounted at process
    start (see the lazy output mount). At import time /app/output is then the bare,
    root-owned bind mount and unwritable by appuser, so an eager mkdir would raise
    PermissionError and break the whole import (config is imported by both gunicorn
    and the entrypoint's deferral guard). The dirs are (re)created at point of use
    once the volume is mounted post-login, so a failure here is safe to swallow."""
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


IMAGES_DIR = Path(os.environ.get('COMFY_OUTPUT_DIR', '/tmp/comfy-images'))
_best_effort_mkdir(IMAGES_DIR)
# Temporary mask storage — kept separate from IMAGES_DIR so mask files never
# appear in review grids, slideshows, or bulk-delete/archive operations.
MASKS_DIR = IMAGES_DIR / '.masks'
_best_effort_mkdir(MASKS_DIR)
# Temporary inpaint source images — when the user draws on the image in the mask
# editor, the original + drawing are composited into a temporary source image used
# only for that one inpaint job. Kept out of IMAGES_DIR so it never appears in
# galleries; consumed and deleted once the job uploads it to ComfyUI.
INPAINT_INPUTS_DIR = IMAGES_DIR / '.inpaint-inputs'
_best_effort_mkdir(INPAINT_INPUTS_DIR)
# Persistent reference assets (the /references table): desktop-uploaded reference
# videos and audio clips that aren't gallery media. Kept dot-prefixed so they never
# surface in review grids/slideshows/bulk-delete, but — unlike masks/inpaint inputs —
# these are NOT single-use: a reference is reused across many generations and must
# survive reload, so files here persist with stable /references-file/<name> URLs.
REFERENCES_DIR = IMAGES_DIR / '.references'
_best_effort_mkdir(REFERENCES_DIR)
# Per-image seed index (the right-click "Copy seed" menu item): a flat JSON map of
# output filename -> the seed that produced it, written by every generation. A single
# dot-prefixed FILE rather than a directory of sidecars, so it can't be mistaken for
# gallery media: select_images() filters on MEDIA_EXTS, so this is never listed by
# /api/images nor swept into an archive. See seed_store.py.
SEEDS_FILE = IMAGES_DIR / '.seeds.json'
# Cap on the number of remembered seeds. Past this the store drops entries whose
# image no longer exists, then oldest-first — so archiving (which moves files out of
# IMAGES_DIR) self-heals with no hook of its own.
SEED_STORE_MAX = 5000

# Archive config — the /archive-* commands copy images into an encrypted volume
# and then delete the originals (move semantics). The container is unprivileged
# and can't mount the volume itself, so it asks a root host agent (shipped as the
# archive-agent .deb) to run zuluCrypt-cli over a Unix socket. The volume is
# encrypted with SECRET_KEY (the deployment's single secret) and is auto-created
# on first archive if the file is absent; the passphrase is sent to the agent per
# request — the agent never stores it. The agent mounts on the host at a directory
# bind-mounted into the container (with rshared propagation) as ARCHIVE_MOUNT_DIR.
ARCHIVE_VOLUME = os.environ.get('ARCHIVE_VOLUME', '')          # host path to encrypted volume
ARCHIVE_SIZE = os.environ.get('ARCHIVE_SIZE', '20G')           # size of the volume auto-created on first archive
ARCHIVE_AGENT_SOCKET = os.environ.get('ARCHIVE_AGENT_SOCKET', '/run/archive-agent.sock')
ARCHIVE_MOUNT_DIR = Path(os.environ.get('ARCHIVE_MOUNT_DIR', '/app/archive'))
# Marker file the agent writes at the volume root on mount. We refuse to delete
# originals unless this is visible here — proof the encrypted volume actually
# propagated into the container and we're not writing to plain disk. Keep in
# sync with MARKER_NAME in packaging/agent/archive-agent.
ARCHIVE_MARKER = '.comfy-archive'

# Live-output encryption (opt-in). When OUTPUT_VOLUME is set, the container
# entrypoint asks the host agent to create-if-missing + mount a LUKS volume at
# IMAGES_DIR before serving, and to unmount it on stop — so generated images are
# encrypted at rest whenever the container isn't running. We refuse to generate
# if the mount marker isn't visible here (the agent drops it on mount, same as
# the archive flow): proof the encrypted volume actually propagated in, so a
# bind/propagation failure never silently writes plaintext images to disk.
OUTPUT_VOLUME = os.environ.get('OUTPUT_VOLUME', '')   # host path to the output volume
OUTPUT_MARKER = ARCHIVE_MARKER                        # same marker file the agent drops
# Size of the output volume auto-created on first mount (mirrors ARCHIVE_SIZE).
OUTPUT_SIZE = os.environ.get('OUTPUT_SIZE', '20G')
# Optional distinct passphrase for the output volume. When unset the volume is keyed
# on SECRET_KEY (the deployment's single secret). Once a UI login password is set the
# volume is re-keyed to a password-derived passphrase (see crypto_key); this value is
# only the FIRST-migration/bootstrap key for the output volume.
OUTPUT_PASSWORD = os.environ.get('OUTPUT_PASSWORD', '')

# Filesystem check (/fscheck). e2fsck can only run on an unmounted volume, so the
# output volume — mounted for the container's whole life — is checked at startup
# (before mount, in the entrypoint) rather than on demand; the archive volume
# (normally unmounted) is checked live. The output check's result is written here
# by the entrypoint's `agent_client check-output` and read back by /api/fscheck.
# This path must be a plain container path OUTSIDE IMAGES_DIR (the check runs
# before the output volume is mounted, so a path on it would be shadowed).
OUTPUT_FSCHECK_RESULT = Path(os.environ.get('OUTPUT_FSCHECK_RESULT', '/tmp/comfy-output-fscheck.json'))
# Client-side timeout (seconds) for a fsck request to the agent. Must exceed the
# agent's own e2fsck ceiling (E2FSCK_TIMEOUT_SECONDS = 900) so the agent returns
# its result before the socket read gives up.
FSCK_TIMEOUT = int(os.environ.get('FSCK_TIMEOUT', '1200'))

# Still-image outputs (and acceptable inputs), rendered in the browser via <img>.
# Animated GIF/WebP also live here — they play natively in an <img>.
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
# True video outputs (e.g. from a VHS_VideoCombine node), rendered via <video>.
VIDEO_EXTS = {".mp4", ".webm"}
# Any output file we'll serve/list/delete — image or video.
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS
# Reference audio clips accepted by the /references table (MiniMax H3 R2V). Not
# gallery media — only ever uploaded to REFERENCES_DIR and fed into a workflow.
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}
# MiniMax H3's total reference-file budget. A reference video charges once per track
# fed to the workflow, so a clip contributing both its video and its audio costs 2.
# Mirrored client-side as REFERENCE_MAX_FILES in static/js/state.js.
REFERENCE_MAX_FILES = 12
# Bypassable optimisations in the video workflows, toggled per run from /video-settings.
# Each key names a "[opt:<key>] ..." marked node in the template (see
# workflow.bypass_optimisation_nodes); a key absent from a template just bypasses
# nothing. Mirrored client-side as VIDEO_OPTIMIZATIONS in static/js/state.js.
VIDEO_OPTIMIZATIONS = ("turbo", "cache", "sage", "sol", "spectrum")
AUTO_PURGE_SECONDS = int(os.environ.get('AUTO_PURGE_SECONDS', '300'))

# Idle session lock: after this many seconds with no authenticated request and no
# running job, log everyone off, forget the in-memory login password and close the
# encrypted volumes (see idle_lock.py and app._idle_lock_down). Logging back in
# re-opens the output volume via the existing lazy-mount path. 0 disables.
IDLE_TIMEOUT_SECONDS = int(os.environ.get('IDLE_TIMEOUT_SECONDS', '7200'))
