import base64
import json
import shutil
import subprocess
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
    list_removal_workflows, list_upscaler_workflows, list_workflow_names,
    load_loras, load_server_catalogue, parse_loras_from_prompt, resolve_workflow,
)
from ComfyServer import ComfyServer
from config import (
    ARCHIVE_AGENT_SOCKET, ARCHIVE_MARKER, ARCHIVE_MOUNT_DIR,
    ARCHIVE_SIZE, ARCHIVE_VOLUME,
    BUILD_VERSION, COMFY_FACEDETAILER_DIR, COMFY_FACEDETAILER_WORKFLOW,
    COMFY_GENERATION_DIR, COMFY_IMAGE2IMAGE_DIR, COMFY_IMAGE2IMAGE_WORKFLOW,
    COMFY_IMAGE2VIDEO_DIR, COMFY_IMAGE2VIDEO_WORKFLOW,
    COMFY_INPAINTING_DIR, COMFY_INPAINTING_WORKFLOW,
    COMFY_REMOVAL_DIR, COMFY_REMOVAL_WORKFLOW,
    COMFY_SERVER, COMFY_SERVER_OS, COMFY_UPSCALER_DIR,
    COMFY_UPSCALER_WORKFLOW, COMFY_WORKFLOW, COMFY_WORKFLOW_DIR,
    IMAGE_EXTS, IMAGES_DIR, MEDIA_EXTS, OUTPUT_MARKER, OUTPUT_VOLUME, PASSWORD, SECRET_KEY, USERNAME,
    VIDEO_EXTS,
)
from generation_service import (
    cancel_auto_purge, get_last_sent_workflow, jobs, jobs_lock,
    run_generation, start_generation_job, start_sequence_job,
)
from image_store import (
    MAX_MASK_BYTES, output_storage_error,
    register_draw_token, register_mask_token,
    resolve_draw_image, resolve_input_image, resolve_mask,
    select_images,
)
from persistence import (
    delete_session, list_sessions, load_aliases, load_macros, load_session,
    save_aliases, save_macros, save_session, slugify,
)
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
        default_removal_workflow=COMFY_REMOVAL_WORKFLOW,
        build_version=BUILD_VERSION,
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


@app.route("/api/removal-workflows")
@login_required
def api_removal_workflows():
    return jsonify(list_removal_workflows())


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


@app.route("/api/extract-last-frame", methods=["POST"])
@login_required
def api_extract_last_frame():
    """Extract the final frame of a generated video and save it as a PNG.

    Powers the scissors (✂) overlay on video results: it pulls the last frame
    out of the clip with ffmpeg, writes it to IMAGES_DIR with a permanent
    /images/ URL, and the browser drops it at the bottom of the chat like any
    other generated image. This enables last-frame video continuity — the
    extracted frame can be edited, do-over'd, or fed back into image2video to
    continue the sequence while processing the frames in between.
    """
    err = output_storage_error()
    if err:
        return err
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url is required"}), 400

    filename = url.rsplit("/", 1)[-1]
    safe = secure_filename(filename)
    if not safe or safe != filename:
        return jsonify({"error": "Invalid filename"}), 400
    src = IMAGES_DIR / safe
    if src.suffix.lower() not in VIDEO_EXTS:
        return jsonify({"error": "Not a video"}), 400
    if not src.is_file():
        return jsonify({"error": "Video not found"}), 404

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"{timestamp}_lastframe_{uuid.uuid4().hex[:8]}.png"
    out_path = IMAGES_DIR / out_name
    # -sseof -1 seeks to ~1s before the end (cheap — ffmpeg only decodes the
    # tail); -update 1 rewrites the single output file for every decoded frame,
    # so the file left behind is the very last frame of the clip.
    cmd = [
        "ffmpeg", "-nostdin", "-y", "-sseof", "-1", "-i", str(src),
        "-update", "1", "-q:v", "2", str(out_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
    except FileNotFoundError:
        return jsonify({"error": "ffmpeg is not available"}), 500
    except subprocess.TimeoutExpired:
        out_path.unlink(missing_ok=True)
        return jsonify({"error": "Frame extraction timed out"}), 500
    if proc.returncode != 0 or not out_path.is_file():
        out_path.unlink(missing_ok=True)
        detail = proc.stderr.decode("utf-8", "replace").strip()[-300:]
        return jsonify({"error": f"Could not extract frame: {detail}"}), 500
    return jsonify({"url": f"/images/{out_name}"})


def _probe_video_info(path):
    """Return {width, height, has_audio, duration} for a video via ffprobe.

    Any field that cannot be determined is None. Used to normalise clips to a
    common resolution and to drive audio handling (fades + silent fill) before
    concatenation; if ffprobe is missing or fails the caller falls back to
    joining clips as a silent montage.
    """
    info = {"width": None, "height": None, "has_audio": False, "duration": None}
    cmd = [
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return info
    if proc.returncode != 0:
        return info
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except ValueError:
        return info
    for stream in data.get("streams", []):
        codec_type = stream.get("codec_type")
        if codec_type == "video" and info["width"] is None:
            try:
                info["width"] = int(stream["width"])
                info["height"] = int(stream["height"])
            except (KeyError, ValueError, TypeError):
                pass
        elif codec_type == "audio":
            info["has_audio"] = True
    try:
        info["duration"] = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        pass
    return info


@app.route("/api/composite-videos", methods=["POST"])
@login_required
def api_composite_videos():
    """Concatenate several session videos into one, in the given order.

    Powers /splice-session: the browser sends an ordered list of
    /images/ video URLs; ffmpeg concatenates them (re-encoding to the first
    clip's resolution so clips of differing sizes still join cleanly) and the
    result is written to IMAGES_DIR and dropped at the bottom of the chat.

    Audio is preserved: each clip's audio is faded in at its start and out at
    its end so the joins are gentle, and clips that have no audio are backed
    by matching silence so the streams line up. If no clip carries audio (or
    durations are unknown) the output is a silent montage.
    """
    err = output_storage_error()
    if err:
        return err
    data = request.get_json(force=True)
    urls = data.get("urls") or []
    if not isinstance(urls, list) or len(urls) < 2:
        return jsonify({"error": "Need at least two videos to composite"}), 400

    srcs = []
    for url in urls:
        filename = str(url).rsplit("/", 1)[-1]
        safe = secure_filename(filename)
        if not safe or safe != filename:
            return jsonify({"error": f"Invalid filename: {filename}"}), 400
        src = IMAGES_DIR / safe
        if src.suffix.lower() not in VIDEO_EXTS:
            return jsonify({"error": f"Not a video: {filename}"}), 400
        if not src.is_file():
            return jsonify({"error": f"Video not found: {filename}"}), 404
        srcs.append(src)

    n = len(srcs)
    infos = [_probe_video_info(s) for s in srcs]

    # Normalise every clip to the first video's resolution; the concat filter
    # requires matching dimensions, so clips of differing sizes are scaled.
    first = infos[0]
    scale = (
        f"scale={first['width']}:{first['height']},"
        if first["width"] and first["height"]
        else ""
    )

    # Include audio only when at least one clip carries it and every clip's
    # duration is known — silent fill and fade-out timing both need durations.
    keep_audio = any(i["has_audio"] for i in infos) and all(
        i["duration"] for i in infos
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"{timestamp}_composite_{uuid.uuid4().hex[:8]}.mp4"
    out_path = IMAGES_DIR / out_name

    cmd = ["ffmpeg", "-nostdin", "-y"]
    for src in srcs:
        cmd += ["-i", str(src)]

    parts = [f"[{i}:v]{scale}setsar=1,format=yuv420p[v{i}]" for i in range(n)]

    if keep_audio:
        # Clips without their own audio get a silent track of matching length,
        # added as extra lavfi inputs after the real clips (indices n, n+1, …).
        fade = 0.5
        audio_label = {}
        extra_idx = n
        for i, info in enumerate(infos):
            dur = info["duration"]
            fd = min(fade, dur / 2)
            if info["has_audio"]:
                chain = (
                    f"[{i}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
                    f"afade=t=in:st=0:d={fd:.3f},"
                    f"afade=t=out:st={dur - fd:.3f}:d={fd:.3f}[a{i}]"
                )
                parts.append(chain)
                audio_label[i] = f"[a{i}]"
            else:
                cmd += [
                    "-f", "lavfi", "-t", f"{dur:.3f}",
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                ]
                parts.append(f"[{extra_idx}:a]aformat=sample_rates=44100:"
                             f"channel_layouts=stereo[a{i}]")
                audio_label[i] = f"[a{i}]"
                extra_idx += 1
        concat_inputs = "".join(f"[v{i}]{audio_label[i]}" for i in range(n))
        parts.append(f"{concat_inputs}concat=n={n}:v=1:a=1[outv][outa]")
        tail = [
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(out_path),
        ]
    else:
        concat_inputs = "".join(f"[v{i}]" for i in range(n))
        parts.append(f"{concat_inputs}concat=n={n}:v=1:a=0[outv]")
        tail = ["-map", "[outv]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(out_path)]

    cmd += ["-filter_complex", ";".join(parts)] + tail
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=600)
    except FileNotFoundError:
        return jsonify({"error": "ffmpeg is not available"}), 500
    except subprocess.TimeoutExpired:
        out_path.unlink(missing_ok=True)
        return jsonify({"error": "Compositing timed out"}), 500
    if proc.returncode != 0 or not out_path.is_file():
        out_path.unlink(missing_ok=True)
        detail = proc.stderr.decode("utf-8", "replace").strip()[-300:]
        return jsonify({"error": f"Could not composite videos: {detail}"}), 500
    return jsonify({"url": f"/images/{out_name}"})


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


def _parse_video_settings(data):
    """Extract and validate the video settings from a JSON request dict.

    All values are optional; when present they fill the
    <DURATION>/<FRAMES>/<FPS> and <VIDEO_WIDTH>/<VIDEO_HEIGHT> placeholders in
    image2video workflows. The client (/video-settings) keeps duration/frames/fps
    mutually consistent (frames = duration × fps), so this only sanity-checks each
    value individually. Video resolution is kept distinct from the image-resolution
    path (width/height) since video models have very different size constraints.

    Returns (settings_dict, None) on success, or (None, error_response) on failure.
    The dict has keys duration, frames, fps, video_width, video_height.
    """
    def _pos_int(raw, label):
        value = int(raw) if raw is not None else None
        if value is not None and value < 1:
            raise ValueError(f"{label} must be a positive integer")
        return value

    try:
        raw_duration = data.get("duration")
        duration = float(raw_duration) if raw_duration is not None else None
        if duration is not None and duration <= 0:
            return None, (jsonify({"error": "duration must be positive"}), 400)
    except (TypeError, ValueError):
        return None, (jsonify({"error": "duration must be a number"}), 400)
    try:
        frames       = _pos_int(data.get("frames"),       "frames")
        fps          = _pos_int(data.get("fps"),          "fps")
        video_width  = _pos_int(data.get("video_width"),  "video_width")
        video_height = _pos_int(data.get("video_height"), "video_height")
    except (TypeError, ValueError) as e:
        msg = str(e) if str(e).endswith("positive integer") else "frames, fps and video resolution must be integers"
        return None, (jsonify({"error": msg}), 400)
    return {
        "duration": duration, "frames": frames, "fps": fps,
        "video_width": video_width, "video_height": video_height,
    }, None


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
    _, image_path, err = resolve_input_image(image_url)
    if err:
        return err

    # Optional end frame for first-frame/last-frame interpolation. When present the
    # i2v template's <INPUT_LAST_FRAME> guide is enabled; when absent the run is a
    # plain single-image image2video (see run_generation).
    last_frame_url = (data.get("last_frame") or "").strip()
    last_frame_path = None
    if last_frame_url:
        _, last_frame_path, err = resolve_input_image(last_frame_url)
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

    vs, err = _parse_video_settings(data)
    if err:
        return err
    assert vs is not None  # err is None here, so vs is populated

    err = output_storage_error()
    if err:
        return err

    # Unlike image2image/upscale/face-detail, image2video does not replace its
    # source — the original image is kept alongside the new video — so the video
    # gets its own (newest) mtime rather than inheriting the source's position.
    job_id = start_generation_job(
        prompt, [], server_address, server_os, workflow_name,
        workflow_dir=COMFY_IMAGE2VIDEO_DIR, input_image=image_path,
        input_last_frame=last_frame_path,
        duration=vs["duration"], frames=vs["frames"], fps=vs["fps"],
        video_width=vs["video_width"], video_height=vs["video_height"],
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


@app.route("/api/remove", methods=["POST"])
@login_required
def api_remove():
    """Run an object-removal workflow (e.g. LaMa) over a previously generated image.

    Like /api/inpaint but without a required prompt — removal models fill in the
    background from context alone, so <PROMPT> is absent from the workflow template.
    """
    data = request.get_json(force=True)

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

    available = list_removal_workflows()
    workflow_name, err = resolve_workflow(
        data.get("workflow") or COMFY_REMOVAL_WORKFLOW, available, "removal"
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
        workflow_dir=COMFY_REMOVAL_DIR,
        input_image=image_path, input_mask=mask_path,
        preserve_mtime_from=safe,
    )
    return jsonify({"job_id": job_id})


def _parse_sequence_request(data):
    """Validate a /sequence-style request, returning (master, count, replacements).

    Raises ValueError with a user-facing message if the master prompt is missing.
    """
    master = (data.get("prompt") or "").strip()
    if not master:
        raise ValueError("A master prompt is required")

    try:
        count = int(data.get("count", 15))
    except (ValueError, TypeError):
        count = 15
    count = max(1, min(count, 64))

    # Replacements arrive as a list of [from, to] pairs applied to each prompt
    # after it comes back from Grok.
    replacements = []
    for pair in data.get("replacements") or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0]:
            replacements.append((str(pair[0]), str(pair[1])))

    return master, count, replacements


@app.route("/api/sequence", methods=["POST"])
@login_required
def api_sequence():
    """Turn a single master prompt into a sequence of prompts via Grok.

    Runs the (slow) Grok call as a tracked job and returns a job_id immediately;
    the client watches /api/progress/<job_id> for the result and can cancel it
    via /api/cancel/<job_id>, exactly like a ComfyUI generation. The returned
    prompts are then fed back through /api/generate one after another (the same
    flow as /multi-prompt).
    """
    data = request.get_json(force=True)
    try:
        master, count, replacements = _parse_sequence_request(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    job_id = start_sequence_job(master, count, replacements, video=False)
    return jsonify({"job_id": job_id})


@app.route("/api/video-sequence", methods=["POST"])
@login_required
def api_video_sequence():
    """Like /api/sequence, but Grok also returns an action and audio prompt per shot.

    The client generates each still image from the `prompt` field only; the
    `action`/`audio` are remembered against the resulting image and folded into
    the video prompt later if the image is turned into a video.
    """
    data = request.get_json(force=True)
    try:
        master, count, replacements = _parse_sequence_request(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    job_id = start_sequence_job(master, count, replacements, video=True)
    return jsonify({"job_id": job_id})


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

    # ComfyUI job: tell the server to interrupt the running prompt.
    prompt_id = job.get("prompt_id")
    if prompt_id:
        try:
            ComfyServer(job["server"]).interrupt(prompt_id)
        except Exception as e:
            # Best-effort: the poll loop still aborts via the cancel event.
            print(f"Interrupt failed for {job['server']}/{prompt_id}: {e}", flush=True)

    # Grok sequence job: close the HTTP session to abort the in-flight request.
    session = job.get("session")
    if session is not None:
        try:
            session.close()
        except Exception as e:
            # Best-effort: the worker still aborts via the cancel event.
            print(f"Closing Grok session failed for {job_id}: {e}", flush=True)

    return jsonify({"ok": True})


@app.route("/api/progress/<job_id>")
@login_required
def api_progress(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    channel = job["channel"]

    def event_stream():
        # Replay everything emitted so far. A returning client (browser dropped
        # the original SSE, phone lost signal, etc.) gets the full history
        # including the terminal done/error/cancelled event and its asset URLs.
        cached = channel.snapshot()
        for msg in cached:
            yield f"data: {msg}\n\n"
        idx = len(cached)

        # If the job already finished before we arrived, stop after replay.
        with jobs_lock:
            status = (jobs.get(job_id) or {}).get("status")
        if status in ("done", "error", "cancelled"):
            return

        # Stream further events, sending a keep-alive ping every 25s.
        while True:
            new_events, closed = channel.next_after(idx, timeout=25)
            if new_events:
                for msg in new_events:
                    yield f"data: {msg}\n\n"
                idx += len(new_events)
                last = json.loads(new_events[-1])
                if last.get("type") in ("done", "error", "cancelled"):
                    break
            else:
                if closed:
                    break
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/jobs")
@login_required
def api_jobs():
    """Return the last 10 ComfyUI generation jobs the server is/was running.

    Grok prompt-sequence jobs (kind == "sequence") are excluded — they're not
    long-running enough to need a recovery UI and the user didn't ask for them.
    Newest first.
    """
    with jobs_lock:
        items = []
        for job_id, rec in jobs.items():
            if rec.get("kind") not in ("image", "video"):
                continue
            items.append({
                "job_id": job_id,
                "status": rec.get("status"),
                "kind": rec.get("kind"),
                "workflow_name": rec.get("workflow_name"),
                "summary": rec.get("summary"),
                "prompt": rec.get("prompt"),
                "started_at": rec.get("started_at"),
                "finished_at": rec.get("finished_at"),
                "assets": list(rec.get("assets") or rec.get("images") or []),
                "error": rec.get("error"),
                "server": rec.get("server"),
            })
    items.sort(key=lambda r: r.get("started_at") or 0, reverse=True)
    return jsonify(items[:10])


@app.route("/api/jobs/<job_id>", methods=["DELETE"])
@login_required
def api_dismiss_job(job_id):
    """Remove a finished job from the tracked list. Does not delete its asset."""
    with jobs_lock:
        rec = jobs.get(job_id)
        if not rec:
            return jsonify({"error": "Job not found"}), 404
        if rec.get("status") not in ("done", "error", "cancelled"):
            return jsonify({"error": "Job is still running"}), 409
        jobs.pop(job_id, None)
    return jsonify({"dismissed": job_id})


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
    if path.suffix.lower() not in MEDIA_EXTS:
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
        if p.suffix.lower() not in MEDIA_EXTS:
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
    if not ARCHIVE_VOLUME:
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
                "password": SECRET_KEY,
                # Self-provision on first archive if the volume file is absent
                # (mirrors the output volume). Safe on an existing volume: the
                # agent's open(volume, "xb") never clobbers one that exists.
                "create": True,
                "size": ARCHIVE_SIZE,
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
            # Session archives are prefixed 001_, 002_, … to lock in the user's
            # drag-sorted order on disk; other scopes keep original names.
            copied = []
            for i, src in enumerate(files):
                fname = f"{i + 1:03d}_{src.name}" if scope == "session" else src.name
                dest = dest_dir / fname
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
# Macros
# ---------------------------------------------------------------------------

@app.route("/api/macros")
@login_required
def api_macros():
    return jsonify(load_macros())


@app.route("/api/macros", methods=["POST"])
@login_required
def api_macro_create():
    data = request.json or {}
    name = (data.get("name") or "").strip()
    steps = data.get("steps") or []
    if not name or " " in name or "/" in name:
        return jsonify({"error": "Macro name cannot be empty or contain spaces/slashes"}), 400
    if not steps:
        return jsonify({"error": "Macro must have at least one step"}), 400
    macros = load_macros()
    updated = name in macros
    macros[name] = [s for s in steps if isinstance(s, str) and s.strip()]
    try:
        save_macros(macros)
    except OSError as e:
        return jsonify({"error": f"Could not save macros: {e}"}), 500
    return jsonify({"ok": True, "name": name, "updated": updated})


@app.route("/api/macros/<macro_name>", methods=["DELETE"])
@login_required
def api_macro_delete(macro_name):
    macros = load_macros()
    if macro_name not in macros:
        return jsonify({"error": "Macro not found"}), 404
    del macros[macro_name]
    try:
        save_macros(macros)
    except OSError as e:
        return jsonify({"error": f"Could not save macros: {e}"}), 500
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Last-sent workflow
# ---------------------------------------------------------------------------

@app.route("/api/last-sent-workflow")
@login_required
def api_last_sent_workflow():
    """Return the last workflow submitted to ComfyUI with all replacements applied."""
    record = get_last_sent_workflow()
    if record is None:
        return jsonify({"error": "No workflow has been submitted yet"}), 404
    return jsonify({
        "workflow": record["workflow"],
        "workflow_name": record["workflow_name"],
        "server": record["server"],
        "submitted_at": record["submitted_at"],
    })


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
