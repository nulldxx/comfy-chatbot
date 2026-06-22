import os
from pathlib import Path

BUILD_VERSION = os.environ.get('BUILD_VERSION', 'unknown')
USERNAME = os.environ.get('APP_USERNAME', 'user')
PASSWORD = os.environ.get('APP_PASSWORD', 'password')
SECRET_KEY = os.environ.get('SECRET_KEY', 'your-secret-key-change-this-in-production')

COMFY_SERVER = os.environ.get('COMFY_SERVER', '192.168.1.135:8000')
COMFY_SERVER_OS = os.environ.get('COMFY_SERVER_OS', 'unix')
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

IMAGES_DIR = Path(os.environ.get('COMFY_OUTPUT_DIR', '/tmp/comfy-images'))
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
# Temporary mask storage — kept separate from IMAGES_DIR so mask files never
# appear in review grids, slideshows, or bulk-delete/archive operations.
MASKS_DIR = IMAGES_DIR / '.masks'
MASKS_DIR.mkdir(parents=True, exist_ok=True)
# Temporary inpaint source images — when the user draws on the image in the mask
# editor, the original + drawing are composited into a temporary source image used
# only for that one inpaint job. Kept out of IMAGES_DIR so it never appears in
# galleries; consumed and deleted once the job uploads it to ComfyUI.
INPAINT_INPUTS_DIR = IMAGES_DIR / '.inpaint-inputs'
INPAINT_INPUTS_DIR.mkdir(parents=True, exist_ok=True)

# Archive config — the /archive-* commands copy images into a password-encrypted
# volume and then delete the originals (move semantics). The container is
# unprivileged and can't mount the volume itself, so it asks a root host agent
# (shipped as the archive-agent .deb) to run zuluCrypt-cli over a Unix
# socket. The volume path + password are sent to the agent per request — the
# agent never stores the password. The agent mounts on the host at a directory
# bind-mounted into the container (with rshared propagation) as ARCHIVE_MOUNT_DIR.
ARCHIVE_VOLUME = os.environ.get('ARCHIVE_VOLUME', '')          # host path to encrypted volume
ARCHIVE_PASSWORD = os.environ.get('ARCHIVE_PASSWORD', '')
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

# Still-image outputs (and acceptable inputs), rendered in the browser via <img>.
# Animated GIF/WebP also live here — they play natively in an <img>.
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
# True video outputs (e.g. from a VHS_VideoCombine node), rendered via <video>.
VIDEO_EXTS = {".mp4", ".webm"}
# Any output file we'll serve/list/delete — image or video.
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS
AUTO_PURGE_SECONDS = int(os.environ.get('AUTO_PURGE_SECONDS', '300'))
