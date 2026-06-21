import os
import re
import json
import uuid
import queue
import threading
from pathlib import Path
from datetime import datetime

import requests
from werkzeug.utils import secure_filename

from config import COMFY_GENERATION_DIR, IMAGES_DIR, AUTO_PURGE_SECONDS
from ComfyServer import ComfyServer, JobCancelled
from grok import GrokError, generate_prompt_sequence, generate_video_prompt_sequence
from workflow import (
    LORA_PLACEHOLDER_RE,
    apply_placeholders, find_placeholders, fill_lora_sentinels,
    strip_lora_nodes, randomize_seeds, lora_path_for_os,
    apply_resolution, apply_steps, apply_denoise,
)

# In-memory job tracking
jobs: dict = {}
jobs_lock = threading.Lock()

# Auto-purge: free GPU memory on a ComfyUI server after a period of idleness.
# Runs server-side so it fires even if the user closes their browser.
purge_state: dict = {}  # server_address -> {"timer": threading.Timer | None, "active": int}
purge_lock = threading.Lock()


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
                   width=None, height=None, steps=None, denoise=None, workflow_dir=None,
                   input_image=None, input_mask=None, input_last_frame=None,
                   preserve_mtime_from=None,
                   cleanup_input_image=False, duration=None, frames=None, fps=None):
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
        if loras:
            names = ", ".join(f"{n} ({s})" for n, s in loras)
            send("progress", message=f"LoRAs: {names}")
        # Fill the <DENOISE> placeholder for templates that use it. apply_denoise()
        # below additionally covers templates with a literal denoise value.
        if denoise is not None:
            mapping["DENOISE"] = denoise

        # Video duration/frames/fps placeholders for image2video workflows. These
        # are bare numeric slots (like <DENOISE>); the UI keeps them mutually
        # consistent (frames = duration × fps) via /video-settings.
        if duration is not None:
            mapping["DURATION"] = duration
        if frames is not None:
            mapping["FRAMES"] = frames
        if fps is not None:
            mapping["FPS"] = fps
        if duration is not None or frames is not None or fps is not None:
            send("progress", message=f"Video: {frames} frames @ {fps} fps ({duration}s)")

        if input_image is not None:
            send("progress", message="Uploading source image to ComfyUI...")
            try:
                mapping["INPUT_IMAGE"] = server.upload_image(input_image)
            finally:
                # A drawn-hint composite is a single-use temp file; delete it once
                # uploaded. Normal gallery source images (cleanup_input_image=False)
                # are left in place.
                if cleanup_input_image:
                    try:
                        input_image.unlink()
                    except OSError:
                        pass

        if input_mask is not None:
            send("progress", message="Uploading mask to ComfyUI...")
            try:
                mapping["INPUT_MASK"] = server.upload_image(input_mask)
            finally:
                try:
                    input_mask.unlink()
                except OSError:
                    pass

        # First-frame/last-frame conditioning (image2video). The template carries an
        # optional <INPUT_LAST_FRAME> LoadImage and a <LAST_FRAME_BYPASS> boolean that
        # disables the end-frame guide node. When a last frame is supplied we upload it
        # and switch the guide on; when it isn't, we reuse the first frame as a harmless
        # stand-in and leave the guide bypassed, so the workflow behaves exactly like the
        # single-image image2video it is today.
        if "<INPUT_LAST_FRAME>" in template:
            if input_last_frame is not None:
                send("progress", message="Uploading last frame to ComfyUI...")
                mapping["INPUT_LAST_FRAME"] = server.upload_image(input_last_frame)
                mapping["LAST_FRAME_BYPASS"] = "false"
            else:
                # No end frame designated: stand in the first frame and bypass the guide.
                mapping["INPUT_LAST_FRAME"] = mapping.get("INPUT_IMAGE", "")
                mapping["LAST_FRAME_BYPASS"] = "true"

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

        if steps is not None:
            apply_steps(workflow, steps)
            send("progress", message=f"Steps set to {steps}")

        if denoise is not None:
            apply_denoise(workflow, denoise)
            send("progress", message=f"Denoise set to {denoise}")

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


# ---------------------------------------------------------------------------
# Grok prompt-sequence jobs
# ---------------------------------------------------------------------------
#
# A /sequence (or /video-sequence) call makes one potentially-slow HTTP request
# to the Grok API. Rather than block the request thread until it returns, we run
# it as a tracked job — exactly like a ComfyUI generation — so the client can
# watch it over SSE and cancel it with the same ✕ button. Cancellation closes
# the job's requests.Session, which aborts the in-flight call to Grok.

def case_preserving_replace(text, src, dst):
    """Replace every occurrence of ``src`` in ``text`` case-insensitively, adapting
    the replacement's case to the matched text: an ALL-CAPS match yields an
    all-caps replacement, a Capitalised match yields a capitalised replacement,
    otherwise the replacement is used exactly as written. E.g. with src "bird"
    and dst "dog": "bird"→"dog", "Bird"→"Dog", "BIRD"→"DOG"."""
    if not src:
        return text

    def repl(m):
        matched = m.group(0)
        if matched.isupper():
            return dst.upper()
        if matched[:1].isupper():
            return dst[:1].upper() + dst[1:]
        return dst

    return re.sub(re.escape(src), repl, text, flags=re.IGNORECASE)


def run_sequence(job_id, master, count, replacements, video):
    with jobs_lock:
        q = jobs[job_id]["queue"]
        cancel_event = jobs[job_id]["cancel"]
        session = jobs[job_id]["session"]

    def send(msg_type, **kwargs):
        q.put(json.dumps({"type": msg_type, **kwargs}))

    try:
        if cancel_event.is_set():
            raise JobCancelled()

        send("progress", message=f"Asking Grok for {count} {'shot' if video else 'prompt'}(s)…")

        if video:
            shots = generate_video_prompt_sequence(
                master, count, cancel_event=cancel_event, session=session
            )
            out = []
            for shot in shots:
                item = {
                    "prompt": shot.get("prompt", ""),
                    "action": shot.get("action", ""),
                    "audio": shot.get("audio", ""),
                }
                for src, dst in replacements:
                    for key in ("prompt", "action", "audio"):
                        item[key] = case_preserving_replace(item[key], src, dst)
                out.append(item)
        else:
            prompts = generate_prompt_sequence(
                master, count, cancel_event=cancel_event, session=session
            )
            out = []
            for p in prompts:
                for src, dst in replacements:
                    p = case_preserving_replace(p, src, dst)
                out.append(p)

        with jobs_lock:
            jobs[job_id]["status"] = "done"
        send("done", prompts=out, video=video)

    except JobCancelled:
        with jobs_lock:
            jobs[job_id]["status"] = "cancelled"
        send("cancelled", message="Cancelled")
    except GrokError as e:
        # A cancel closes the session, which surfaces as a GrokError from the
        # aborted request — report it as a cancellation, not an error.
        if cancel_event.is_set():
            with jobs_lock:
                jobs[job_id]["status"] = "cancelled"
            send("cancelled", message="Cancelled")
        else:
            with jobs_lock:
                jobs[job_id]["status"] = "error"
            send("error", message=str(e))
    except Exception as e:
        with jobs_lock:
            jobs[job_id]["status"] = "error"
        send("error", message=str(e))
    finally:
        try:
            session.close()
        except Exception:
            pass


def start_sequence_job(master, count, replacements, video):
    """Create a tracked Grok prompt-sequence job; return its job_id."""
    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "status": "pending",
            "queue": queue.Queue(),
            "images": [],
            "cancel": threading.Event(),
            "server": None,
            "prompt_id": None,
            "session": requests.Session(),
        }

    t = threading.Thread(
        target=run_sequence,
        args=(job_id, master, count, replacements, video),
        daemon=True,
    )
    t.start()
    return job_id
