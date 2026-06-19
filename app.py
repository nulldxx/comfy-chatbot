import re
import os
import json
import uuid
import queue
import shutil
import threading
from pathlib import Path
from datetime import datetime
from functools import wraps
from flask import (
    Flask, render_template, jsonify, request,
    session, redirect, url_for, Response, send_from_directory,
)
from werkzeug.utils import secure_filename
from agent_client import send as agent_send
from ComfyServer import ComfyServer, JobCancelled
from grok import generate_prompt_sequence, GrokError
from workflow import (
    LORA_TAG_RE, LORA_PLACEHOLDER_RE,
    apply_placeholders, find_placeholders, fill_lora_sentinels,
    strip_lora_nodes, randomize_seeds, lora_path_for_os,
    apply_resolution, fill_placeholders_for_validation,
)

app = Flask(__name__)

# Build/version info — baked in at image-build time via the BUILD_VERSION
# build-arg (see Dockerfile / scripts/docker-build). Logged once on startup.
BUILD_VERSION = os.environ.get('BUILD_VERSION', 'unknown')
print(f"comfy-chatbot starting — build {BUILD_VERSION}", flush=True)

# Auth
USERNAME = os.environ.get('APP_USERNAME', 'user')
PASSWORD = os.environ.get('APP_PASSWORD', 'password')
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-change-this-in-production')

# ComfyUI config
COMFY_SERVER = os.environ.get('COMFY_SERVER', '192.168.1.135:8000')
COMFY_SERVER_OS = os.environ.get('COMFY_SERVER_OS', 'unix')
COMFY_WORKFLOW = os.environ.get('COMFY_WORKFLOW', 'z_image_turbo_api')
COMFY_WORKFLOW_DIR = Path(os.environ.get('COMFY_WORKFLOW_DIR', '/app/workflows'))
COMFY_LORAS_FILE = Path(os.environ.get('COMFY_LORAS_FILE', '/app/workflows/loras.json'))
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

IMAGES_DIR = Path(os.environ.get('COMFY_OUTPUT_DIR', '/tmp/comfy-images'))
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

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

# Only one mount/copy/unmount cycle at a time (gunicorn runs gthread workers).
archive_lock = threading.Lock()
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# In-memory job tracking
jobs: dict = {}
jobs_lock = threading.Lock()

# Auto-purge: free GPU memory on a ComfyUI server after a period of idleness.
# Runs server-side so it fires even if the user closes their browser.
AUTO_PURGE_SECONDS = int(os.environ.get('AUTO_PURGE_SECONDS', '300'))
purge_state: dict = {}  # server_address -> {"timer": threading.Timer | None, "active": int}
purge_lock = threading.Lock()

# ---------------------------------------------------------------------------
# LoRA helpers
# ---------------------------------------------------------------------------

def load_server_catalogue():
    servers_file = COMFY_WORKFLOW_DIR / "servers.json"
    if not servers_file.is_file():
        return []
    try:
        return json.loads(servers_file.read_text()).get("servers", [])
    except Exception:
        return []


def load_loras():
    if not COMFY_LORAS_FILE.is_file():
        return []
    try:
        data = json.loads(COMFY_LORAS_FILE.read_text())
        loras = data.get("loras", [])
        return [
            entry if isinstance(entry, dict) else {"name": entry, "strength": 1.0}
            for entry in loras
        ]
    except Exception:
        return []


def lora_catalogue_strength(name):
    for entry in load_loras():
        if entry.get("name") == name:
            return str(entry["strength"])
    return None


def parse_loras_from_prompt(text):
    """Strip <lora:name> / <lora:name:strength> tags and return (clean_prompt, [(name, strength)])."""
    loras = []

    def replacer(m):
        name = m.group(1)
        strength = m.group(2) or lora_catalogue_strength(name) or "1.0"
        loras.append((name, strength))
        return ""

    clean = LORA_TAG_RE.sub(replacer, text).strip()
    # Collapse any double-spaces left by removed tags
    clean = re.sub(r'  +', ' ', clean)
    return clean, loras


# ---------------------------------------------------------------------------
# Auto-purge timers
# ---------------------------------------------------------------------------

def _auto_purge(server_address):
    with purge_lock:
        state = purge_state.get(server_address)
        if state:
            state["timer"] = None
    try:
        ComfyServer(server_address).free_memory()
        print(f"Auto-purged GPU memory on {server_address} after {AUTO_PURGE_SECONDS}s idle", flush=True)
    except Exception as e:
        print(f"Auto-purge failed for {server_address}: {e}", flush=True)


def _cancel_purge_timer_locked(state):
    if state["timer"] is not None:
        state["timer"].cancel()
        state["timer"] = None


def purge_generation_started(server_address):
    """Cancel any pending purge and mark a generation as running on this server."""
    with purge_lock:
        state = purge_state.setdefault(server_address, {"timer": None, "active": 0})
        _cancel_purge_timer_locked(state)
        state["active"] += 1


def purge_generation_finished(server_address):
    """Schedule a purge once the last running generation on this server ends."""
    with purge_lock:
        state = purge_state.setdefault(server_address, {"timer": None, "active": 0})
        state["active"] = max(0, state["active"] - 1)
        if state["active"] == 0:
            _cancel_purge_timer_locked(state)
            timer = threading.Timer(AUTO_PURGE_SECONDS, _auto_purge, args=(server_address,))
            timer.daemon = True
            timer.start()
            state["timer"] = timer


def cancel_auto_purge(server_address):
    with purge_lock:
        state = purge_state.get(server_address)
        if state:
            _cancel_purge_timer_locked(state)


# ---------------------------------------------------------------------------
# Background generation thread
# ---------------------------------------------------------------------------

def run_generation(job_id, prompt, loras, server_address, server_os, workflow_name,
                   width=None, height=None, workflow_dir=None, input_image=None,
                   preserve_mtime_from=None):
    with jobs_lock:
        q = jobs[job_id]["queue"]
        cancel_event = jobs[job_id]["cancel"]

    def send(msg_type, **kwargs):
        q.put(json.dumps({"type": msg_type, **kwargs}))

    def progress(msg_str):
        if msg_str == ".":
            q.put(json.dumps({"type": "tick"}))
        else:
            send("progress", message=msg_str)

    purge_generation_started(server_address)
    try:
        base_dir = (workflow_dir or COMFY_GENERATION_DIR).resolve()
        name_with_ext = workflow_name if workflow_name.endswith(".json") else f"{workflow_name}.json"
        # Resolve and confine to base_dir: workflow_name is client-supplied (and
        # may name a subfolder), so a "../" can't be allowed to escape the dir.
        workflow_path = (base_dir / name_with_ext).resolve()
        if not workflow_path.is_relative_to(base_dir) or not workflow_path.is_file():
            raise FileNotFoundError(f"Workflow template not found: {workflow_name}")

        send("progress", message=f"Loading workflow: {workflow_path.name}")
        template = workflow_path.read_text()

        server = ComfyServer(server_address)

        mapping = {"PROMPT": prompt}
        for i, (name, strength) in enumerate(loras, start=1):
            mapping[f"LORA_{i}_NAME"] = lora_path_for_os(name, server_os)
            mapping[f"LORA_{i}_STRENGTH"] = strength

        if input_image is not None:
            send("progress", message="Uploading source image to ComfyUI...")
            mapping["INPUT_IMAGE"] = server.upload_image(input_image)

        filled = apply_placeholders(template, mapping)

        remaining = find_placeholders(filled)
        lora_unfilled = [t for t in remaining if LORA_PLACEHOLDER_RE.fullmatch(t)]
        other_unfilled = [t for t in remaining if not LORA_PLACEHOLDER_RE.fullmatch(t)]

        if other_unfilled:
            raise ValueError(f"Unfilled workflow placeholders: {', '.join(other_unfilled)}")

        if lora_unfilled:
            filled = fill_lora_sentinels(filled)

        try:
            workflow = json.loads(filled)
        except json.JSONDecodeError as e:
            raise ValueError(f"Workflow is not valid JSON after substitution: {e}")

        if lora_unfilled:
            workflow, removed = strip_lora_nodes(workflow)
            if removed:
                send("progress", message=f"Skipping {len(removed)} unused LoRA node(s)")

        if "nodes" in workflow:
            send("progress", message="Converting UI-format workflow to API format...")
            workflow = server.convert_ui_to_api_format(workflow)

        if width and height:
            apply_resolution(workflow, width, height)
            send("progress", message=f"Resolution set to {width}×{height}")

        if randomize_seeds(workflow):
            send("progress", message="Randomized seed values")

        if cancel_event.is_set():
            raise JobCancelled()

        send("progress", message=f"Submitting to {server_address}...")
        prompt_id = server.submit_workflow(workflow)
        with jobs_lock:
            jobs[job_id]["prompt_id"] = prompt_id
        send("progress", message=f"Queued (ID: {prompt_id[:8]}…) — generating")

        prompt_data = server.poll_status(prompt_id, 600, progress, cancel_event=cancel_event)
        send("progress", message="Downloading images...")

        images = server.get_output_images(prompt_data)
        if not images:
            raise ValueError("No images produced by workflow")

        # Download to a temp dir, then rename each file with a timestamp prefix
        tmp_dir = IMAGES_DIR / job_id
        tmp_dir.mkdir(parents=True, exist_ok=True)
        downloaded = server.download_images(images, tmp_dir)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        image_urls = []
        dest_paths = []
        for fp in downloaded:
            fp = Path(fp)
            dest = IMAGES_DIR / f"{timestamp}_{fp.name}"
            fp.rename(dest)
            dest_paths.append(dest)
            image_urls.append(f"/images/{dest.name}")
        tmp_dir.rmdir()

        # When this job replaces an existing image (a do-over, or an accepted
        # face-detail / upscale), copy the source image's mtime onto the result
        # so mtime-ordered views (/review-all, /review-today, the slideshow)
        # keep the original position instead of jumping the new image to the top.
        if preserve_mtime_from:
            src_name = secure_filename(Path(preserve_mtime_from).name)
            src_path = IMAGES_DIR / src_name
            if src_name and src_path.is_file():
                src_stat = src_path.stat()
                for dest in dest_paths:
                    try:
                        os.utime(dest, (src_stat.st_atime, src_stat.st_mtime))
                    except OSError:
                        pass

        with jobs_lock:
            jobs[job_id]["status"] = "done"
            jobs[job_id]["images"] = image_urls

        send("done", images=image_urls)

    except JobCancelled:
        with jobs_lock:
            jobs[job_id]["status"] = "cancelled"
        send("cancelled", message="Cancelled")
    except Exception as e:
        with jobs_lock:
            jobs[job_id]["status"] = "error"
        send("error", message=str(e))
    finally:
        purge_generation_finished(server_address)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == USERNAME and request.form.get("password") == PASSWORD:
            session["authenticated"] = True
            return redirect(request.args.get("next") or url_for("index"))
        return render_template("login.html", error="Invalid username or password")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("authenticated", None)
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def index():
    return render_template(
        "index.html",
        default_server=COMFY_SERVER,
        default_server_os=COMFY_SERVER_OS,
        default_workflow=COMFY_WORKFLOW,
        default_face_workflow=COMFY_FACEDETAILER_WORKFLOW,
        default_upscale_workflow=COMFY_UPSCALER_WORKFLOW,
        default_image2image_workflow=COMFY_IMAGE2IMAGE_WORKFLOW,
    )


@app.route("/api/loras")
@login_required
def api_loras():
    return jsonify(load_loras())


@app.route("/api/servers")
@login_required
def api_servers():
    servers = load_server_catalogue()
    if not servers:
        # Synthesise one entry from env-var defaults so the UI always has something
        host, _, port = COMFY_SERVER.rpartition(":")
        servers = [{"name": "default", "host": host or COMFY_SERVER, "port": int(port or 8000), "os": COMFY_SERVER_OS}]
    return jsonify(servers)


@app.route("/api/add-server", methods=["POST"])
@login_required
def api_add_server():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    host = (data.get("host") or "").strip()
    os_type = (data.get("os") or "").strip().lower()
    try:
        port = int(data.get("port", 0))
        if port < 1 or port > 65535:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"error": "Port must be a number between 1 and 65535"}), 400

    if not name:
        return jsonify({"error": "Name is required"}), 400
    if not host:
        return jsonify({"error": "Host is required"}), 400
    if os_type not in ("unix", "windows"):
        return jsonify({"error": "OS must be 'unix' or 'windows'"}), 400

    servers_file = COMFY_WORKFLOW_DIR / "servers.json"
    servers = load_server_catalogue()

    # Replace existing entry with the same name, otherwise append
    entry = {"name": name, "host": host, "port": port, "os": os_type}
    servers = [s for s in servers if s.get("name") != name]
    servers.append(entry)

    try:
        servers_file.write_text(json.dumps({"servers": servers}, indent=2))
    except OSError as e:
        return jsonify({"error": f"Could not save servers.json: {e}"}), 500

    return jsonify(entry)


def list_workflow_names(base_dir):
    """Relative '/'-joined workflow names (no .json) found recursively under base_dir."""
    if not base_dir.is_dir():
        return []
    return sorted(
        f.relative_to(base_dir).with_suffix("").as_posix()
        for f in base_dir.glob("**/*.json")
    )


@app.route("/api/workflows")
@login_required
def api_workflows():
    return jsonify(list_workflow_names(COMFY_GENERATION_DIR))


def list_facedetailer_workflows():
    return list_workflow_names(COMFY_FACEDETAILER_DIR)


@app.route("/api/facedetailer-workflows")
@login_required
def api_facedetailer_workflows():
    return jsonify(list_facedetailer_workflows())


def list_upscaler_workflows():
    return list_workflow_names(COMFY_UPSCALER_DIR)


@app.route("/api/upscaler-workflows")
@login_required
def api_upscaler_workflows():
    return jsonify(list_upscaler_workflows())


def list_image2image_workflows():
    return list_workflow_names(COMFY_IMAGE2IMAGE_DIR)


@app.route("/api/image2image-workflows")
@login_required
def api_image2image_workflows():
    return jsonify(list_image2image_workflows())


@app.route("/api/purge", methods=["POST"])
@login_required
def api_purge():
    server_address = (request.get_json(force=True, silent=True) or {}).get("server") or COMFY_SERVER
    try:
        ComfyServer(server_address).free_memory()
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    cancel_auto_purge(server_address)
    return jsonify({"ok": True})


@app.route("/api/upload-workflow", methods=["POST"])
@login_required
def api_upload_workflow():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No file selected"}), 400
    if not f.filename.lower().endswith(".json"):
        return jsonify({"error": "File must be a .json workflow"}), 400

    content = f.read()
    try:
        json.loads(fill_placeholders_for_validation(content.decode("utf-8")))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return jsonify({"error": f"Invalid workflow file: {e}"}), 400

    filename = secure_filename(f.filename)
    dest = COMFY_GENERATION_DIR / filename
    try:
        COMFY_GENERATION_DIR.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    except OSError as e:
        return jsonify({"error": f"Could not save file: {e}"}), 500

    return jsonify({"name": dest.stem})


def output_storage_error():
    """If live-output encryption is enabled (OUTPUT_VOLUME set) but the encrypted
    volume isn't mounted here, return a Flask (response, status) tuple so callers
    refuse to start a generation that would write images to plain disk. Returns
    None when storage is healthy or encryption is disabled."""
    if OUTPUT_VOLUME and not (IMAGES_DIR / OUTPUT_MARKER).exists():
        return jsonify({"error": "Encrypted output volume is not mounted; "
                                 "refusing to write images to plain disk."}), 503
    return None


def start_generation_job(prompt, loras, server_address, server_os, workflow_name, **kwargs):
    """Create a tracked job and spawn its generation thread; return the job_id.

    Extra kwargs (width/height, workflow_dir, input_image) are forwarded to
    run_generation.
    """
    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "status": "pending",
            "queue": queue.Queue(),
            "images": [],
            "cancel": threading.Event(),
            "server": server_address,
            "prompt_id": None,
        }

    t = threading.Thread(
        target=run_generation,
        args=(job_id, prompt, loras, server_address, server_os, workflow_name),
        kwargs=kwargs,
        daemon=True,
    )
    t.start()
    return job_id


@app.route("/api/generate", methods=["POST"])
@login_required
def api_generate():
    data = request.get_json(force=True)
    raw_prompt = (data.get("prompt") or "").strip()
    if not raw_prompt:
        return jsonify({"error": "prompt is required"}), 400

    prompt, loras = parse_loras_from_prompt(raw_prompt)
    if not prompt:
        return jsonify({"error": "Prompt is empty after removing LoRA tags"}), 400

    server_address = data.get("server") or COMFY_SERVER
    server_os      = data.get("server_os") or COMFY_SERVER_OS
    workflow_name  = data.get("workflow") or COMFY_WORKFLOW

    width  = data.get("width")
    height = data.get("height")
    if width is not None:
        try:
            width = int(width)
        except (ValueError, TypeError):
            return jsonify({"error": "width must be an integer"}), 400
    if height is not None:
        try:
            height = int(height)
        except (ValueError, TypeError):
            return jsonify({"error": "height must be an integer"}), 400

    err = output_storage_error()
    if err:
        return err

    # A do-over passes the image it replaces so the result can inherit its mtime
    # and keep its place in mtime-ordered reviews/slideshow.
    preserve_mtime_from = (data.get("preserve_mtime_from") or "").strip() or None

    job_id = start_generation_job(
        prompt, loras, server_address, server_os, workflow_name,
        workflow_dir=COMFY_GENERATION_DIR,
        width=width, height=height, preserve_mtime_from=preserve_mtime_from,
    )
    return jsonify({"job_id": job_id})


def _resolve_input_image(image_url):
    """Return (safe_name, path, None) on success or (None, None, error_response) on failure."""
    filename = image_url.rsplit("/", 1)[-1]
    safe = secure_filename(filename)
    if not safe or safe != filename:
        return None, None, (jsonify({"error": "Invalid image filename"}), 400)
    if Path(safe).suffix.lower() not in IMAGE_EXTS:
        return None, None, (jsonify({"error": "Source image must be a supported image type"}), 400)
    image_path = IMAGES_DIR / safe
    if not image_path.is_file():
        return None, None, (jsonify({"error": "Source image not found"}), 404)
    return safe, image_path, None


def _resolve_workflow(workflow_name, available, kind):
    """Return (resolved_name, None) or (None, error_response) after validating against an allowlist."""
    if workflow_name:
        name = workflow_name[:-5] if workflow_name.endswith(".json") else workflow_name
        if name not in available:
            return None, (jsonify({"error": f"Unknown {kind} workflow: {workflow_name}"}), 400)
        return name, None
    elif available:
        return available[0], None
    else:
        return None, (jsonify({"error": f"No {kind} workflows available"}), 400)


@app.route("/api/face-detail", methods=["POST"])
@login_required
def api_face_detail():
    """Run a face-detailer workflow over a previously generated image.

    The workflow is loaded from the facedetailer/ subdir and supports the usual
    <PROMPT> and <lora:...> tags plus an <INPUT_IMAGE> placeholder, which is
    filled with the uploaded source image.
    """
    data = request.get_json(force=True)
    raw_prompt = (data.get("prompt") or "").strip()
    if not raw_prompt:
        return jsonify({"error": "prompt is required"}), 400

    prompt, loras = parse_loras_from_prompt(raw_prompt)
    if not prompt:
        return jsonify({"error": "Prompt is empty after removing LoRA tags"}), 400

    image_url = (data.get("image") or "").strip()
    if not image_url:
        return jsonify({"error": "image is required"}), 400
    safe, image_path, err = _resolve_input_image(image_url)
    if err:
        return err

    available = list_facedetailer_workflows()
    workflow_name, err = _resolve_workflow(
        data.get("workflow") or COMFY_FACEDETAILER_WORKFLOW, available, "face-detailer"
    )
    if err:
        return err

    server_address = data.get("server") or COMFY_SERVER
    server_os      = data.get("server_os") or COMFY_SERVER_OS

    err = output_storage_error()
    if err:
        return err

    job_id = start_generation_job(
        prompt, loras, server_address, server_os, workflow_name,
        workflow_dir=COMFY_FACEDETAILER_DIR, input_image=image_path,
        preserve_mtime_from=safe,
    )
    return jsonify({"job_id": job_id})


@app.route("/api/upscale", methods=["POST"])
@login_required
def api_upscale():
    """Run an upscaler workflow over a previously generated image.

    Like /api/face-detail this loads a workflow from the upscaler/ subdir and
    fills its <INPUT_IMAGE> placeholder with the uploaded source image — but it
    takes no prompt and no LoRA tags.
    """
    data = request.get_json(force=True)

    image_url = (data.get("image") or "").strip()
    if not image_url:
        return jsonify({"error": "image is required"}), 400
    safe, image_path, err = _resolve_input_image(image_url)
    if err:
        return err

    available = list_upscaler_workflows()
    workflow_name, err = _resolve_workflow(
        data.get("workflow") or COMFY_UPSCALER_WORKFLOW, available, "upscaler"
    )
    if err:
        return err

    server_address = data.get("server") or COMFY_SERVER
    server_os      = data.get("server_os") or COMFY_SERVER_OS

    err = output_storage_error()
    if err:
        return err

    job_id = start_generation_job(
        "", [], server_address, server_os, workflow_name,
        workflow_dir=COMFY_UPSCALER_DIR, input_image=image_path,
        preserve_mtime_from=safe,
    )
    return jsonify({"job_id": job_id})


@app.route("/api/image2image", methods=["POST"])
@login_required
def api_image2image():
    """Run an image2image workflow over a previously generated image.

    Like /api/face-detail this loads a workflow from the image2image/ subdir and
    fills its <INPUT_IMAGE> placeholder with the uploaded source image, plus the
    usual <PROMPT> and <lora:...> tags. Unlike face-detail the prompt is
    optional — the caller may re-run the workflow over the image with an empty
    prompt (e.g. when the original generation prompt isn't available).
    """
    data = request.get_json(force=True)
    raw_prompt = (data.get("prompt") or "").strip()
    prompt, loras = parse_loras_from_prompt(raw_prompt)

    image_url = (data.get("image") or "").strip()
    if not image_url:
        return jsonify({"error": "image is required"}), 400
    safe, image_path, err = _resolve_input_image(image_url)
    if err:
        return err

    available = list_image2image_workflows()
    workflow_name, err = _resolve_workflow(
        data.get("workflow") or COMFY_IMAGE2IMAGE_WORKFLOW, available, "image2image"
    )
    if err:
        return err

    server_address = data.get("server") or COMFY_SERVER
    server_os      = data.get("server_os") or COMFY_SERVER_OS

    err = output_storage_error()
    if err:
        return err

    job_id = start_generation_job(
        prompt, loras, server_address, server_os, workflow_name,
        workflow_dir=COMFY_IMAGE2IMAGE_DIR, input_image=image_path,
        preserve_mtime_from=safe,
    )
    return jsonify({"job_id": job_id})


@app.route("/api/sequence", methods=["POST"])
@login_required
def api_sequence():
    """Turn a single master prompt into a sequence of prompts via Grok.

    The client then feeds each returned prompt back through /api/generate,
    one after another (the same flow as /multi).
    """
    data = request.get_json(force=True)
    master = (data.get("prompt") or "").strip()
    if not master:
        return jsonify({"error": "A master prompt is required"}), 400

    try:
        count = int(data.get("count", 15))
    except (ValueError, TypeError):
        count = 15
    count = max(1, min(count, 64))

    # Replacements arrive as a list of [from, to] pairs and are applied to each
    # prompt after it comes back from Grok.
    replacements = []
    for pair in data.get("replacements") or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0]:
            replacements.append((str(pair[0]), str(pair[1])))

    try:
        prompts = generate_prompt_sequence(master, count)
    except GrokError as e:
        return jsonify({"error": str(e)}), 502

    out = []
    for p in prompts:
        for src, dst in replacements:
            p = p.replace(src, dst)
        out.append(p)

    return jsonify({"prompts": out})


@app.route("/api/cancel/<job_id>", methods=["POST"])
@login_required
def api_cancel(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    job["cancel"].set()
    prompt_id = job.get("prompt_id")
    if prompt_id:
        try:
            ComfyServer(job["server"]).interrupt(prompt_id)
        except Exception as e:
            # Best-effort: the poll loop still aborts via the cancel event.
            print(f"Interrupt failed for {job['server']}/{prompt_id}: {e}", flush=True)

    return jsonify({"ok": True})


@app.route("/api/progress/<job_id>")
@login_required
def api_progress(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    def event_stream():
        q = job["queue"]
        while True:
            try:
                msg = q.get(timeout=25)
                yield f"data: {msg}\n\n"
                parsed = json.loads(msg)
                if parsed.get("type") in ("done", "error", "cancelled"):
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/images/<filename>")
@login_required
def serve_image(filename):
    response = send_from_directory(str(IMAGES_DIR), filename)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/images/<filename>", methods=["DELETE"])
@login_required
def api_delete_image(filename):
    safe = secure_filename(filename)
    if not safe or safe != filename:
        return jsonify({"error": "Invalid filename"}), 400
    path = IMAGES_DIR / safe
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return jsonify({"error": "Invalid filename"}), 400
    if not path.is_file():
        return jsonify({"error": "Image not found"}), 404
    path.unlink()
    return jsonify({"deleted": safe})


@app.route("/api/images", methods=["DELETE"])
@login_required
def api_delete_all_images():
    if not IMAGES_DIR.is_dir():
        return jsonify({"deleted": 0})
    deleted = 0
    failed = []
    for p in IMAGES_DIR.iterdir():
        if p.suffix.lower() not in IMAGE_EXTS:
            continue
        try:
            p.unlink()
            deleted += 1
        except OSError as exc:
            failed.append(f"{p.name}: {exc}")
    if failed:
        return jsonify({"deleted": deleted, "error": "; ".join(failed)}), 500
    return jsonify({"deleted": deleted})


def _select_images(scope, filenames=None):
    """Resolve an archive/listing scope to a list of image Paths in IMAGES_DIR.

    - "all"     -> every image file.
    - "today"   -> images whose mtime is today (mirrors the /api/images filter).
    - "session" -> the supplied filenames, validated like api_delete_image.
    Raises ValueError on an unknown scope or an invalid session filename.
    """
    if not IMAGES_DIR.is_dir():
        return []
    if scope == "session":
        selected = []
        for name in (filenames or []):
            safe = secure_filename(name)
            if not safe or safe != name:
                raise ValueError(f"Invalid filename: {name}")
            path = IMAGES_DIR / safe
            if path.suffix.lower() not in IMAGE_EXTS:
                raise ValueError(f"Invalid filename: {name}")
            if path.is_file():
                selected.append(path)
        return selected
    files = [p for p in IMAGES_DIR.iterdir() if p.suffix.lower() in IMAGE_EXTS]
    if scope == "today":
        today = datetime.now().date()
        files = [
            p for p in files
            if datetime.fromtimestamp(p.stat().st_mtime).date() == today
        ]
    elif scope != "all":
        raise ValueError(f"Unknown scope: {scope}")
    return files


@app.route("/api/images")
@login_required
def api_images():
    scope = "today" if request.args.get("filter") == "today" else "all"
    files = _select_images(scope)
    files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonify([f"/images/{p.name}" for p in files])


def _agent_request(payload: dict, timeout: float = 120.0) -> dict:
    """Send one newline-delimited JSON request to the host archive agent over its
    Unix socket and return the parsed JSON reply. Raises RuntimeError on transport
    failure so callers can surface a clean error to the user. The transport lives
    in agent_client so the container entrypoint can reuse it."""
    return agent_send(payload, ARCHIVE_AGENT_SOCKET, timeout)


def _slugify_archive_name(name):
    """Turn a user-supplied archive name into a safe folder name: lower-cased,
    with runs of non-alphanumeric characters collapsed to single hyphens and
    leading/trailing hyphens stripped. E.g. "Man walking on Beach" ->
    "man-walking-on-beach". Returns "" if nothing usable remains, so callers
    can fall back to a generated name."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug


@app.route("/api/archive", methods=["POST"])
@login_required
def api_archive():
    if not ARCHIVE_VOLUME or not ARCHIVE_PASSWORD:
        return jsonify({"error": "Archiving is not configured on the server."}), 503

    body = request.get_json(silent=True) or {}
    scope = body.get("scope")
    try:
        files = _select_images(scope, body.get("filenames"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if not files:
        return jsonify({"archived": 0})

    # Use the caller's name (slugified) as the staging folder, falling back to a
    # random guid when no usable name was supplied.
    folder = _slugify_archive_name(body.get("name")) or uuid.uuid4().hex
    with archive_lock:
        try:
            resp = _agent_request({
                "action": "mount",
                "volume": ARCHIVE_VOLUME,
                "password": ARCHIVE_PASSWORD,
            })
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 502
        if not resp.get("ok"):
            return jsonify({"error": resp.get("error", "mount failed")}), 502

        try:
            # Safety: the agent writes ARCHIVE_MARKER at the volume root on mount.
            # If it isn't visible the encrypted volume didn't propagate in (e.g.
            # MOUNT_DIR and this bind source disagree), so writing here would land
            # on plain disk. Abort before deleting anything.
            if not (ARCHIVE_MOUNT_DIR / ARCHIVE_MARKER).exists():
                return jsonify({"error": "archive volume not mounted (safety check "
                                         "failed); no files were deleted"}), 500
            dest_dir = ARCHIVE_MOUNT_DIR / "staging" / folder
            dest_dir.mkdir(parents=True, exist_ok=True)
            # Copy + verify every file before deleting any original (move semantics).
            copied = []
            for src in files:
                dest = dest_dir / src.name
                # copyfile (data only) rather than copy2: exFAT can't store
                # POSIX permissions, so copystat's chmod raises EPERM there.
                shutil.copyfile(src, dest)
                if dest.stat().st_size != src.stat().st_size:
                    raise OSError(f"size mismatch after copying {src.name}")
                copied.append(src)
            for src in copied:
                src.unlink()
        except OSError as exc:
            return jsonify({"error": f"archive failed: {exc}"}), 500
        finally:
            try:
                _agent_request({"action": "unmount", "volume": ARCHIVE_VOLUME})
            except RuntimeError as exc:
                app.logger.warning("archive agent unmount failed: %s", exc)

    return jsonify({"archived": len(files), "folder": folder})


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
