import base64
import json
import queue
import shutil
import threading
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (
    Flask, Response, jsonify, redirect, render_template,
    request, send_from_directory, session, url_for,
)
from werkzeug.utils import secure_filename

from agent_client import send as agent_send
from catalogue import (
    list_facedetailer_workflows, list_image2image_workflows,
    list_image2video_workflows, list_inpainting_workflows,
    list_upscaler_workflows, list_workflow_names, load_loras,
    load_server_catalogue, parse_loras_from_prompt, resolve_workflow,
)
from ComfyServer import ComfyServer
from config import (
    ARCHIVE_AGENT_SOCKET, ARCHIVE_MARKER, ARCHIVE_MOUNT_DIR,
    ARCHIVE_PASSWORD, ARCHIVE_VOLUME,
    BUILD_VERSION, COMFY_FACEDETAILER_DIR, COMFY_FACEDETAILER_WORKFLOW,
    COMFY_GENERATION_DIR, COMFY_IMAGE2IMAGE_DIR, COMFY_IMAGE2IMAGE_WORKFLOW,
    COMFY_IMAGE2VIDEO_DIR, COMFY_IMAGE2VIDEO_WORKFLOW,
    COMFY_INPAINTING_DIR, COMFY_INPAINTING_WORKFLOW,
    COMFY_SERVER, COMFY_SERVER_OS, COMFY_UPSCALER_DIR,
    COMFY_UPSCALER_WORKFLOW, COMFY_WORKFLOW, COMFY_WORKFLOW_DIR,
    IMAGE_EXTS, IMAGES_DIR, OUTPUT_MARKER, OUTPUT_VOLUME, PASSWORD, SECRET_KEY, USERNAME,
)
from generation_service import (
    cancel_auto_purge, jobs, jobs_lock, run_generation, start_generation_job,
)
from grok import GrokError, generate_prompt_sequence
from image_store import (
    MAX_MASK_BYTES, output_storage_error,
    register_draw_token, register_mask_token,
    resolve_draw_image, resolve_input_image, resolve_mask,
    select_images,
)
from persistence import (
    delete_session, list_sessions, load_aliases, load_session,
    save_aliases, save_session, slugify,
)
from workflow import fill_placeholders_for_validation

app = Flask(__name__)
app.secret_key = SECRET_KEY

print(f"comfy-chatbot starting — build {BUILD_VERSION}", flush=True)

# Only one archive mount/copy/unmount cycle at a time (gunicorn runs gthread workers).
archive_lock = threading.Lock()


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
# Main page
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
        default_inpainting_workflow=COMFY_INPAINTING_WORKFLOW,
        default_image2video_workflow=COMFY_IMAGE2VIDEO_WORKFLOW,
    )


# ---------------------------------------------------------------------------
# Catalogue endpoints (LoRAs, servers, workflows)
# ---------------------------------------------------------------------------

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
    return jsonify(list_workflow_names(COMFY_GENERATION_DIR))


@app.route("/api/facedetailer-workflows")
@login_required
def api_facedetailer_workflows():
    return jsonify(list_facedetailer_workflows())


@app.route("/api/upscaler-workflows")
@login_required
def api_upscaler_workflows():
    return jsonify(list_upscaler_workflows())


@app.route("/api/image2image-workflows")
@login_required
def api_image2image_workflows():
    return jsonify(list_image2image_workflows())


@app.route("/api/inpainting-workflows")
@login_required
def api_inpainting_workflows():
    return jsonify(list_inpainting_workflows())


@app.route("/api/image2video-workflows")
@login_required
def api_image2video_workflows():
    return jsonify(list_image2video_workflows())


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


# ---------------------------------------------------------------------------
# Image upload / token endpoints
# ---------------------------------------------------------------------------

@app.route("/api/upload-mask", methods=["POST"])
@login_required
def api_upload_mask():
    """Accept a base64-encoded PNG mask from the browser and save it to MASKS_DIR.

    Returns a short-lived opaque token the client passes back in the /api/inpaint
    call. The token is bound to the uploading session user and consumed atomically
    on use, so it cannot be replayed or claimed by another session. Masks are stored
    outside IMAGES_DIR so they never appear in review grids or slideshow views.
    """
    data = request.get_json(force=True)
    b64 = (data.get("data") or "").strip()
    if not b64:
        return jsonify({"error": "data is required"}), 400
    if len(b64) > MAX_MASK_BYTES * 4 // 3 + 4:
        return jsonify({"error": "Mask too large (10 MB limit)"}), 413
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return jsonify({"error": "invalid base64"}), 400
    if len(raw) > MAX_MASK_BYTES:
        return jsonify({"error": "Mask too large (10 MB limit)"}), 413
    token = register_mask_token(session.get("user"), raw)
    return jsonify({"token": token})


@app.route("/api/upload-inpaint-image", methods=["POST"])
@login_required
def api_upload_inpaint_image():
    """Accept a base64-encoded PNG (original image + the user's drawn hint) from the browser.

    Returns a short-lived opaque token the client passes back in the /api/inpaint call as
    `draw_token`. Same single-use, session-bound lifecycle as /api/upload-mask. Stored
    outside IMAGES_DIR so the temporary composite never appears in galleries; consumed and
    deleted once the inpaint job uploads it to ComfyUI.
    """
    data = request.get_json(force=True)
    b64 = (data.get("data") or "").strip()
    if not b64:
        return jsonify({"error": "data is required"}), 400
    _MAX_DRAW_BYTES = 50 * 1024 * 1024  # 50 MB decoded — matches /api/save-image
    if len(b64) > _MAX_DRAW_BYTES * 4 // 3 + 4:
        return jsonify({"error": "Image too large (50 MB limit)"}), 413
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return jsonify({"error": "invalid base64"}), 400
    if len(raw) > _MAX_DRAW_BYTES:
        return jsonify({"error": "Image too large (50 MB limit)"}), 413
    token = register_draw_token(session.get("user"), raw)
    return jsonify({"token": token})


@app.route("/api/save-image", methods=["POST"])
@login_required
def api_save_image():
    """Accept a base64-encoded PNG from the browser and save it to IMAGES_DIR.

    Used by the client-side selective-composite flow: after the user paints a mask
    over the face-detail result and the browser composites the two images, the
    resulting PNG is uploaded here so it gets a permanent /images/ URL and appears
    in review/slideshow views like any other generated image.
    """
    data = request.get_json(force=True)
    b64 = (data.get("data") or "").strip()
    if not b64:
        return jsonify({"error": "data is required"}), 400
    _MAX_COMPOSITE_BYTES = 50 * 1024 * 1024  # 50 MB decoded
    if len(b64) > _MAX_COMPOSITE_BYTES * 4 // 3 + 4:
        return jsonify({"error": "Image too large (50 MB limit)"}), 413
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return jsonify({"error": "invalid base64"}), 400
    if len(raw) > _MAX_COMPOSITE_BYTES:
        return jsonify({"error": "Image too large (50 MB limit)"}), 413
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_composite_{uuid.uuid4().hex[:8]}.png"
    path = IMAGES_DIR / filename
    path.write_bytes(raw)
    return jsonify({"url": f"/images/{filename}"})


@app.route("/api/import-image", methods=["POST"])
@login_required
def api_import_image():
    """Accept an image file dragged into the browser and save it to IMAGES_DIR.

    Lets the user drop an image from outside the app into the current session; it
    gets a permanent /images/ URL and is treated like any other generated image
    (review grids, slideshow, do-over, etc.). The original file extension is
    preserved (restricted to the formats we serve) and the bytes are written
    verbatim — no re-encoding.
    """
    err = output_storage_error()
    if err:
        return err
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No file selected"}), 400
    ext = Path(f.filename).suffix.lower()
    if ext not in IMAGE_EXTS:
        return jsonify({"error": f"Unsupported image type '{ext}'"}), 400

    _MAX_IMPORT_BYTES = 50 * 1024 * 1024  # 50 MB
    raw = f.read()
    if not raw:
        return jsonify({"error": "Empty file"}), 400
    if len(raw) > _MAX_IMPORT_BYTES:
        return jsonify({"error": "Image too large (50 MB limit)"}), 413

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_imported_{uuid.uuid4().hex[:8]}{ext}"
    path = IMAGES_DIR / filename
    try:
        path.write_bytes(raw)
    except OSError as e:
        return jsonify({"error": f"Could not save file: {e}"}), 500
    return jsonify({"url": f"/images/{filename}"})


# ---------------------------------------------------------------------------
# Generation endpoints
# ---------------------------------------------------------------------------

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

    steps = data.get("steps")
    if steps is not None:
        try:
            steps = int(steps)
            if steps < 1:
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({"error": "steps must be a positive integer"}), 400

    err = output_storage_error()
    if err:
        return err

    # A do-over passes the image it replaces so the result can inherit its mtime
    # and keep its place in mtime-ordered reviews/slideshow.
    preserve_mtime_from = (data.get("preserve_mtime_from") or "").strip() or None

    job_id = start_generation_job(
        prompt, loras, server_address, server_os, workflow_name,
        workflow_dir=COMFY_GENERATION_DIR,
        width=width, height=height, steps=steps, preserve_mtime_from=preserve_mtime_from,
    )
    return jsonify({"job_id": job_id})


def _parse_denoise(data):
    """Extract and validate the denoise param from a JSON request dict.

    Returns (denoise_float_or_None, None) on success, or (None, error_response) on failure.
    """
    raw_denoise = data.get("denoise")
    try:
        denoise = float(raw_denoise) if raw_denoise is not None else None
        if denoise is not None and not (0.0 <= denoise <= 1.0):
            return None, (jsonify({"error": "denoise must be between 0.0 and 1.0"}), 400)
    except (TypeError, ValueError):
        return None, (jsonify({"error": "denoise must be a number"}), 400)
    return denoise, None


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
    safe, image_path, err = resolve_input_image(image_url)
    if err:
        return err

    available = list_facedetailer_workflows()
    workflow_name, err = resolve_workflow(
        data.get("workflow") or COMFY_FACEDETAILER_WORKFLOW, available, "face-detailer"
    )
    if err:
        return err

    server_address = data.get("server") or COMFY_SERVER
    server_os      = data.get("server_os") or COMFY_SERVER_OS
    denoise, err = _parse_denoise(data)
    if err:
        return err

    err = output_storage_error()
    if err:
        return err

    job_id = start_generation_job(
        prompt, loras, server_address, server_os, workflow_name,
        workflow_dir=COMFY_FACEDETAILER_DIR, input_image=image_path,
        preserve_mtime_from=safe, denoise=denoise,
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
    safe, image_path, err = resolve_input_image(image_url)
    if err:
        return err

    available = list_upscaler_workflows()
    workflow_name, err = resolve_workflow(
        data.get("workflow") or COMFY_UPSCALER_WORKFLOW, available, "upscaler"
    )
    if err:
        return err

    server_address = data.get("server") or COMFY_SERVER
    server_os      = data.get("server_os") or COMFY_SERVER_OS
    denoise, err = _parse_denoise(data)
    if err:
        return err

    err = output_storage_error()
    if err:
        return err

    job_id = start_generation_job(
        "", [], server_address, server_os, workflow_name,
        workflow_dir=COMFY_UPSCALER_DIR, input_image=image_path,
        preserve_mtime_from=safe, denoise=denoise,
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
    safe, image_path, err = resolve_input_image(image_url)
    if err:
        return err

    available = list_image2image_workflows()
    workflow_name, err = resolve_workflow(
        data.get("workflow") or COMFY_IMAGE2IMAGE_WORKFLOW, available, "image2image"
    )
    if err:
        return err

    server_address = data.get("server") or COMFY_SERVER
    server_os      = data.get("server_os") or COMFY_SERVER_OS
    denoise, err = _parse_denoise(data)
    if err:
        return err

    err = output_storage_error()
    if err:
        return err

    job_id = start_generation_job(
        prompt, loras, server_address, server_os, workflow_name,
        workflow_dir=COMFY_IMAGE2IMAGE_DIR, input_image=image_path,
        preserve_mtime_from=safe, denoise=denoise,
    )
    return jsonify({"job_id": job_id})


@app.route("/api/image2video", methods=["POST"])
@login_required
def api_image2video():
    """Run an image2video workflow over a previously generated image.

    Like /api/image2image this loads a workflow from the image2video/ subdir and
    fills its <INPUT_IMAGE> placeholder with the source image, plus an optional
    <PROMPT>. No LoRA or denoise support in this initial implementation.
    """
    data = request.get_json(force=True)
    raw_prompt = (data.get("prompt") or "").strip()
    prompt, _ = parse_loras_from_prompt(raw_prompt)

    image_url = (data.get("image") or "").strip()
    if not image_url:
        return jsonify({"error": "image is required"}), 400
    safe, image_path, err = resolve_input_image(image_url)
    if err:
        return err

    available = list_image2video_workflows()
    workflow_name, err = resolve_workflow(
        data.get("workflow") or COMFY_IMAGE2VIDEO_WORKFLOW, available, "image2video"
    )
    if err:
        return err

    server_address = data.get("server") or COMFY_SERVER
    server_os      = data.get("server_os") or COMFY_SERVER_OS

    err = output_storage_error()
    if err:
        return err

    job_id = start_generation_job(
        prompt, [], server_address, server_os, workflow_name,
        workflow_dir=COMFY_IMAGE2VIDEO_DIR, input_image=image_path,
        preserve_mtime_from=safe,
    )
    return jsonify({"job_id": job_id})


@app.route("/api/inpaint", methods=["POST"])
@login_required
def api_inpaint():
    """Run an inpainting workflow over a previously generated image using a painted mask.

    Requires both an <INPUT_IMAGE> (the source image) and an <INPUT_MASK> (a B&W PNG
    where white = inpaint area) placeholder in the workflow template, plus the usual
    <PROMPT> / <lora:...> tags. The mask should be uploaded first via /api/upload-mask.
    """
    data = request.get_json(force=True)
    raw_prompt = (data.get("prompt") or "").strip()
    if not raw_prompt:
        return jsonify({"error": "inpainting prompt is required"}), 400
    prompt, loras = parse_loras_from_prompt(raw_prompt)
    if not prompt:
        return jsonify({"error": "Prompt is empty after removing LoRA tags"}), 400

    image_url = (data.get("image") or "").strip()
    if not image_url:
        return jsonify({"error": "image is required"}), 400
    safe, image_path, err = resolve_input_image(image_url)
    if err:
        return err

    mask_token = (data.get("mask") or "").strip()
    if not mask_token:
        return jsonify({"error": "mask is required"}), 400
    mask_path, err = resolve_mask(mask_token, session.get("user"))
    if err:
        return err

    # Optional drawn-hint composite (original image + the user's pen strokes). When
    # present it becomes the ComfyUI INPUT_IMAGE in place of the original, while the
    # original `safe` filename is still used for preserve_mtime_from so the result
    # sorts/replaces correctly in the gallery. The temp file is consumed once.
    draw_token = (data.get("draw_token") or "").strip()
    draw_path = None
    if draw_token:
        draw_path, err = resolve_draw_image(draw_token, session.get("user"))
        if err:
            return err

    available = list_inpainting_workflows()
    workflow_name, err = resolve_workflow(
        data.get("workflow") or COMFY_INPAINTING_WORKFLOW, available, "inpainting"
    )
    if err:
        return err

    server_address = data.get("server") or COMFY_SERVER
    server_os      = data.get("server_os") or COMFY_SERVER_OS
    denoise, err = _parse_denoise(data)
    if err:
        return err

    err = output_storage_error()
    if err:
        return err

    job_id = start_generation_job(
        prompt, loras, server_address, server_os, workflow_name,
        workflow_dir=COMFY_INPAINTING_DIR,
        input_image=draw_path or image_path, input_mask=mask_path,
        preserve_mtime_from=safe,
        denoise=denoise,
        cleanup_input_image=draw_path is not None,
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


# ---------------------------------------------------------------------------
# Job management
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Image serving and management
# ---------------------------------------------------------------------------

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
    if path.suffix.lower() not in IMAGE_EXTS:
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


@app.route("/api/images")
@login_required
def api_images():
    scope = "today" if request.args.get("filter") == "today" else "all"
    files = select_images(scope)
    files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonify([f"/images/{p.name}" for p in files])


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------

def _agent_request(payload: dict, timeout: float = 120.0) -> dict:
    """Send one request to the host archive agent; raise RuntimeError on failure."""
    return agent_send(payload, ARCHIVE_AGENT_SOCKET, timeout)


@app.route("/api/archive", methods=["POST"])
@login_required
def api_archive():
    if not ARCHIVE_VOLUME or not ARCHIVE_PASSWORD:
        return jsonify({"error": "Archiving is not configured on the server."}), 503

    body = request.get_json(silent=True) or {}
    scope = body.get("scope")
    try:
        files = select_images(scope, body.get("filenames"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if not files:
        return jsonify({"archived": 0})

    # Use the caller's name (slugified) as the staging folder, falling back to a
    # random guid when no usable name was supplied.
    folder = slugify(body.get("name")) or uuid.uuid4().hex
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


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------

@app.route("/api/sessions")
@login_required
def api_sessions_list():
    return jsonify(list_sessions())


@app.route("/api/sessions", methods=["POST"])
@login_required
def api_session_save():
    body = request.get_json(force=True) or {}
    raw_name = (body.get("name") or "").strip()
    name = slugify(raw_name)
    if not name:
        return jsonify({"error": "A valid session name is required"}), 400
    try:
        save_session(name, body)
    except OSError as e:
        return jsonify({"error": f"Could not save session: {e}"}), 500
    return jsonify({"ok": True, "name": name})


@app.route("/api/sessions/<name>", methods=["GET"])
@login_required
def api_session_load(name):
    safe = secure_filename(name)
    if not safe or safe != name:
        return jsonify({"error": "Invalid session name"}), 400
    try:
        data = load_session(safe)
    except FileNotFoundError:
        return jsonify({"error": "Session not found"}), 404
    except Exception as e:
        return jsonify({"error": f"Could not read session: {e}"}), 500
    return jsonify(data)


@app.route("/api/sessions/<name>", methods=["DELETE"])
@login_required
def api_session_delete(name):
    safe = secure_filename(name)
    if not safe or safe != name:
        return jsonify({"error": "Invalid session name"}), 400
    try:
        delete_session(safe)
    except FileNotFoundError:
        return jsonify({"error": "Session not found"}), 404
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Prompt aliases
# ---------------------------------------------------------------------------

@app.route("/api/aliases")
@login_required
def api_aliases():
    return jsonify(load_aliases())


@app.route("/api/aliases", methods=["POST"])
@login_required
def api_alias_create():
    data = request.get_json(force=True) or {}
    alias_from = (data.get("from") or "").strip()
    alias_to   = (data.get("to")   or "").strip()
    if not alias_from:
        return jsonify({"error": "from is required"}), 400
    if not alias_to:
        return jsonify({"error": "to is required"}), 400
    if " " in alias_from or "/" in alias_from:
        return jsonify({"error": "alias name cannot contain spaces or slashes"}), 400
    aliases = load_aliases()
    updated = alias_from in aliases
    aliases[alias_from] = alias_to
    try:
        save_aliases(aliases)
    except OSError as e:
        return jsonify({"error": f"Could not save aliases: {e}"}), 500
    return jsonify({"ok": True, "from": alias_from, "to": alias_to, "updated": updated})


@app.route("/api/aliases/<alias_from>", methods=["DELETE"])
@login_required
def api_alias_delete(alias_from):
    aliases = load_aliases()
    if alias_from not in aliases:
        return jsonify({"error": "Alias not found"}), 404
    del aliases[alias_from]
    try:
        save_aliases(aliases)
    except OSError as e:
        return jsonify({"error": f"Could not save aliases: {e}"}), 500
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
