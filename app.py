import re
import os
import json
import random
import uuid
import queue
import threading
from pathlib import Path
from datetime import datetime
from functools import wraps
from flask import (
    Flask, render_template, jsonify, request,
    session, redirect, url_for, Response, send_from_directory,
)
from werkzeug.utils import secure_filename
from ComfyServer import ComfyServer

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

IMAGES_DIR = Path(os.environ.get('COMFY_OUTPUT_DIR', '/tmp/comfy-images'))
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# In-memory job tracking
jobs: dict = {}
jobs_lock = threading.Lock()

# Template placeholder constants (mirrors comfy-runworkflow)
PLACEHOLDER_RE = re.compile(r"<[A-Z0-9_]+>")
LORA_PLACEHOLDER_RE = re.compile(r"<LORA_\d+_(?:NAME|STRENGTH)>")
LORA_NAME_SENTINEL = "__LORA_UNSET__"

# User-facing LoRA tag: <lora:name> or <lora:name:strength>
LORA_TAG_RE = re.compile(r'<lora:([^:>\s]+)(?::([0-9.]+))?>', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Workflow helper functions (ported from comfy-runworkflow)
# ---------------------------------------------------------------------------

def apply_placeholders(text, mapping):
    for key, value in mapping.items():
        escaped = json.dumps(str(value))[1:-1]
        text = text.replace(f"<{key}>", escaped)
    return text


def find_placeholders(text):
    return sorted(set(PLACEHOLDER_RE.findall(text)))


def fill_lora_sentinels(text):
    text = re.sub(r"<LORA_\d+_NAME>", LORA_NAME_SENTINEL, text)
    text = re.sub(r"<LORA_\d+_STRENGTH>", "0", text)
    return text


def strip_lora_nodes(workflow):
    removed = [
        node_id
        for node_id, node in workflow.items()
        if node.get("inputs", {}).get("lora_name") == LORA_NAME_SENTINEL
    ]
    for node_id in removed:
        inputs = workflow[node_id].get("inputs", {})
        passthrough = {0: inputs.get("model")}
        if "clip" in inputs:
            passthrough[1] = inputs.get("clip")
        del workflow[node_id]
        _rewire_references(workflow, node_id, passthrough)
    return workflow, removed


def _rewire_references(workflow, removed_id, passthrough):
    for node in workflow.values():
        for key, value in node.get("inputs", {}).items():
            if isinstance(value, list) and len(value) == 2 and value[0] == removed_id:
                replacement = passthrough.get(value[1])
                if replacement is not None:
                    node["inputs"][key] = replacement


def randomize_seeds(workflow):
    """Replace every seed/noise_seed input in an API-format workflow with a random value."""
    randomized = 0
    for node in workflow.values():
        inputs = node.get("inputs", {})
        for key in ("seed", "noise_seed"):
            if isinstance(inputs.get(key), (int, float)):
                inputs[key] = random.randint(0, 2**64 - 1)
                randomized += 1
    return randomized


def lora_path_for_os(path, os_type):
    if os_type == "windows":
        return path.replace("/", "\\")
    return path


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
# Background generation thread
# ---------------------------------------------------------------------------

def run_generation(job_id, prompt, loras, server_address, server_os, workflow_name):
    with jobs_lock:
        q = jobs[job_id]["queue"]

    def send(msg_type, **kwargs):
        q.put(json.dumps({"type": msg_type, **kwargs}))

    def progress(msg_str):
        if msg_str == ".":
            q.put(json.dumps({"type": "tick"}))
        else:
            send("progress", message=msg_str)

    try:
        name_with_ext = workflow_name if workflow_name.endswith(".json") else f"{workflow_name}.json"
        workflow_path = COMFY_WORKFLOW_DIR / name_with_ext
        if not workflow_path.is_file():
            raise FileNotFoundError(f"Workflow template not found: {workflow_path}")

        send("progress", message=f"Loading workflow: {workflow_path.name}")
        template = workflow_path.read_text()

        mapping = {"PROMPT": prompt}
        for i, (name, strength) in enumerate(loras, start=1):
            mapping[f"LORA_{i}_NAME"] = lora_path_for_os(name, server_os)
            mapping[f"LORA_{i}_STRENGTH"] = strength

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

        server = ComfyServer(server_address)

        if "nodes" in workflow:
            send("progress", message="Converting UI-format workflow to API format...")
            workflow = server.convert_ui_to_api_format(workflow)

        if randomize_seeds(workflow):
            send("progress", message="Randomized seed values")

        send("progress", message=f"Submitting to {server_address}...")
        prompt_id = server.submit_workflow(workflow)
        send("progress", message=f"Queued (ID: {prompt_id[:8]}…) — generating")

        prompt_data = server.poll_status(prompt_id, 600, progress)
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
        for fp in downloaded:
            fp = Path(fp)
            dest = IMAGES_DIR / f"{timestamp}_{fp.name}"
            fp.rename(dest)
            image_urls.append(f"/images/{dest.name}")
        tmp_dir.rmdir()

        with jobs_lock:
            jobs[job_id]["status"] = "done"
            jobs[job_id]["images"] = image_urls

        send("done", images=image_urls)

    except Exception as e:
        with jobs_lock:
            jobs[job_id]["status"] = "error"
        send("error", message=str(e))


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


@app.route("/api/workflows")
@login_required
def api_workflows():
    skip = {"loras.json", "servers.json"}
    workflows = []
    if COMFY_WORKFLOW_DIR.is_dir():
        workflows = [
            f.stem
            for f in sorted(COMFY_WORKFLOW_DIR.glob("*.json"))
            if f.name not in skip
        ]
    return jsonify(workflows)


@app.route("/api/purge", methods=["POST"])
@login_required
def api_purge():
    server_address = (request.get_json(force=True, silent=True) or {}).get("server") or COMFY_SERVER
    try:
        ComfyServer(server_address).free_memory()
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"ok": True})


def _fill_placeholders_for_validation(text):
    """Replace template tokens with dummy values so the file parses as JSON."""
    text = re.sub(r"<LORA_\d+_STRENGTH>", "1.0", text)   # unquoted numeric slots
    text = re.sub(r"<[A-Z0-9_]+>", "placeholder", text)   # all remaining string slots
    return text


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
        json.loads(_fill_placeholders_for_validation(content.decode("utf-8")))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return jsonify({"error": f"Invalid workflow file: {e}"}), 400

    filename = secure_filename(f.filename)
    dest = COMFY_WORKFLOW_DIR / filename
    try:
        dest.write_bytes(content)
    except OSError as e:
        return jsonify({"error": f"Could not save file: {e}"}), 500

    return jsonify({"name": dest.stem})


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

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {"status": "pending", "queue": queue.Queue(), "images": []}

    t = threading.Thread(
        target=run_generation,
        args=(job_id, prompt, loras, server_address, server_os, workflow_name),
        daemon=True,
    )
    t.start()

    return jsonify({"job_id": job_id})


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
                if parsed.get("type") in ("done", "error"):
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


@app.route("/api/images")
@login_required
def api_images():
    if not IMAGES_DIR.is_dir():
        return jsonify([])
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    files = (p for p in IMAGES_DIR.iterdir() if p.suffix.lower() in exts)
    if request.args.get("filter") == "today":
        today = datetime.now().date()
        files = (
            p for p in files
            if datetime.fromtimestamp(p.stat().st_mtime).date() == today
        )
    files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonify([f"/images/{p.name}" for p in files])


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
