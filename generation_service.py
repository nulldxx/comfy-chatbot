import os
import re
import json
import time
import uuid
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
    apply_resolution, apply_steps,
)

# In-memory job tracking. Each job record carries:
#   status:          "pending" | "running" | "done" | "error" | "cancelled"
#   kind:            "image" | "video" | "sequence"
#   workflow_name:   filename of the workflow template (None for sequence jobs)
#   prompt:          user prompt (empty string for upscale/sequence)
#   summary:         short human label for /jobs cards
#   server:          ComfyUI server address (None for sequence jobs)
#   prompt_id:       ComfyUI prompt id once submitted (None otherwise)
#   started_at:      unix time when the job was created
#   finished_at:     unix time of terminal status (None while running)
#   images / assets: list of /images/... URLs produced (assets is the canonical name)
#   error:           string when status == "error"
#   cancel:          threading.Event the client can set via /api/cancel
#   events:          append-only list of JSON-encoded SSE messages (replay log)
#   cond:            threading.Condition used to notify SSE watchers of new events
#   session:         requests.Session for in-flight Grok calls (sequence jobs only)
jobs: dict = {}
jobs_lock = threading.Lock()

# The last workflow submitted to ComfyUI, stored after all placeholder
# substitution, LoRA stripping, resolution/steps overrides, and seed
# randomisation — i.e. exactly what was sent to the server.
_last_sent: dict | None = None
_last_sent_lock = threading.Lock()


def get_last_sent_workflow() -> dict | None:
    """Return a copy of the last submitted workflow record, or None."""
    with _last_sent_lock:
        return dict(_last_sent) if _last_sent is not None else None

# Cap how long a single ComfyUI poll loop will wait for completion. Long video
# renders can easily exceed 10 minutes, so we use 4 hours instead of the old
# 600s cap — cancellation via cancel_event keeps the loop responsive regardless.
COMFY_POLL_TIMEOUT_SECONDS = 4 * 60 * 60

# Eviction bounds for the jobs dict (see _evict_old_jobs). Terminal jobs older
# than the keep window are dropped; we always keep up to MAX_TERMINAL_JOBS most
# recent terminal jobs even if older than the window. Non-terminal jobs are
# never evicted automatically.
MAX_TERMINAL_JOBS = 50
TERMINAL_JOB_KEEP_SECONDS = 24 * 60 * 60
TERMINAL_STATUSES = ("done", "error", "cancelled")


class _JobChannel:
    """Append-only event log with a Condition for reattachable SSE streams.

    Replaces the per-job queue.Queue. send() appends the encoded event to the
    log and notifies all waiters; a new SSE connection can replay every event
    emitted so far and then block on next_after() for further events. This lets
    a returning client (whose browser dropped the original SSE) still see the
    terminal done/error/cancelled message — and the resulting asset URLs.
    """

    def __init__(self):
        self.events: list[str] = []
        self.cond = threading.Condition()
        self.closed = False

    def send(self, encoded: str):
        with self.cond:
            self.events.append(encoded)
            self.cond.notify_all()

    def close(self):
        with self.cond:
            self.closed = True
            self.cond.notify_all()

    def snapshot(self) -> list[str]:
        with self.cond:
            return list(self.events)

    def next_after(self, index: int, timeout: float):
        """Return (new_events_list, closed_flag) for events past ``index``.

        Blocks up to ``timeout`` seconds for at least one new event. Returns an
        empty list and the current closed flag on timeout — the caller treats
        that as a keep-alive opportunity.
        """
        with self.cond:
            if len(self.events) <= index and not self.closed:
                self.cond.wait(timeout=timeout)
            return list(self.events[index:]), self.closed


def _evict_old_jobs_locked():
    """Trim the jobs dict. Caller must hold jobs_lock.

    - Drops terminal jobs older than TERMINAL_JOB_KEEP_SECONDS.
    - If more than MAX_TERMINAL_JOBS terminal jobs remain, drops the oldest
      ones until the cap is met.
    - Never touches non-terminal jobs (pending/running) — they're live.
    """
    now = time.time()
    terminal = [
        (jid, rec) for jid, rec in jobs.items()
        if rec.get("status") in TERMINAL_STATUSES
    ]
    for jid, rec in terminal:
        finished = rec.get("finished_at") or rec.get("started_at") or now
        if now - finished > TERMINAL_JOB_KEEP_SECONDS:
            jobs.pop(jid, None)

    terminal = [
        (jid, rec) for jid, rec in jobs.items()
        if rec.get("status") in TERMINAL_STATUSES
    ]
    if len(terminal) > MAX_TERMINAL_JOBS:
        terminal.sort(key=lambda kv: kv[1].get("finished_at") or kv[1].get("started_at") or 0)
        for jid, _ in terminal[: len(terminal) - MAX_TERMINAL_JOBS]:
            jobs.pop(jid, None)


def _mark_terminal_locked(job_id: str, status: str, **extra):
    """Set terminal status + finished_at on a job. Caller must hold jobs_lock."""
    rec = jobs.get(job_id)
    if not rec:
        return
    rec["status"] = status
    rec["finished_at"] = time.time()
    for k, v in extra.items():
        rec[k] = v


def _build_summary(workflow_name: str | None, prompt: str, kind: str) -> str:
    """Short human-facing label for /jobs cards. Workflow basename + prompt prefix."""
    base = ""
    if workflow_name:
        base = Path(workflow_name).stem
    prompt_clean = (prompt or "").strip().replace("\n", " ")
    if len(prompt_clean) > 60:
        prompt_clean = prompt_clean[:57] + "…"
    label = base or kind
    if prompt_clean:
        return f"{label} · {prompt_clean}"
    return label

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
                   cleanup_input_image=False, duration=None, frames=None, fps=None,
                   video_width=None, video_height=None):
    with jobs_lock:
        channel = jobs[job_id]["channel"]
        cancel_event = jobs[job_id]["cancel"]
        jobs[job_id]["status"] = "running"

    def send(msg_type, **kwargs):
        channel.send(json.dumps({"type": msg_type, **kwargs}))

    def progress(msg_str):
        if msg_str == ".":
            channel.send(json.dumps({"type": "tick"}))
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

        # Video resolution placeholders (<VIDEO_WIDTH>/<VIDEO_HEIGHT>). Kept distinct
        # from the image-resolution path (apply_resolution / currentResolution) since
        # video models have very different size constraints. These are bare numeric
        # slots set via /video-settings.
        if video_width is not None:
            mapping["VIDEO_WIDTH"] = video_width
        if video_height is not None:
            mapping["VIDEO_HEIGHT"] = video_height
        if video_width is not None and video_height is not None:
            send("progress", message=f"Video resolution {video_width}×{video_height}")

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
        # optional <INPUT_LAST_FRAME> LoadImage feeding an LTXVAddGuide node pinned to
        # the final frame (frame_idx = -1); its <LAST_FRAME_STRENGTH> is the on/off
        # toggle. When a last frame is supplied we upload it and set strength 1.0; when
        # it isn't, we reuse the first frame as a harmless stand-in and set strength 0.0
        # so the guide contributes nothing and the workflow behaves exactly like the
        # single-image image2video it is today.
        if "<INPUT_LAST_FRAME>" in template:
            if input_last_frame is not None:
                send("progress", message="Uploading last frame to ComfyUI...")
                mapping["INPUT_LAST_FRAME"] = server.upload_image(input_last_frame)
                mapping["LAST_FRAME_STRENGTH"] = 1.0
            else:
                # No end frame designated: stand in the first frame, guide off.
                mapping["INPUT_LAST_FRAME"] = mapping.get("INPUT_IMAGE", "")
                mapping["LAST_FRAME_STRENGTH"] = 0.0

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

        if randomize_seeds(workflow):
            send("progress", message="Randomized seed values")

        if cancel_event.is_set():
            raise JobCancelled()

        global _last_sent
        with _last_sent_lock:
            _last_sent = {
                "workflow": workflow,
                "workflow_name": workflow_name,
                "server": server_address,
                "submitted_at": time.time(),
            }

        send("progress", message=f"Submitting to {server_address}...")
        prompt_id = server.submit_workflow(workflow)
        with jobs_lock:
            jobs[job_id]["prompt_id"] = prompt_id
        send("progress", message=f"Queued (ID: {prompt_id[:8]}…) — generating")

        prompt_data = server.poll_status(prompt_id, COMFY_POLL_TIMEOUT_SECONDS, progress, cancel_event=cancel_event)
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
            _mark_terminal_locked(job_id, "done", images=image_urls, assets=image_urls)

        send("done", images=image_urls)

    except JobCancelled:
        with jobs_lock:
            _mark_terminal_locked(job_id, "cancelled")
        send("cancelled", message="Cancelled")
    except Exception as e:
        with jobs_lock:
            _mark_terminal_locked(job_id, "error", error=str(e))
        send("error", message=str(e))
    finally:
        channel.close()
        purge_generation_finished(server_address)


def start_generation_job(prompt, loras, server_address, server_os, workflow_name, **kwargs):
    """Create a tracked job and spawn its generation thread; return the job_id.

    Extra kwargs (width/height, workflow_dir, input_image, etc.) are forwarded to
    run_generation. We also use them to classify the job (image vs video) for the
    /jobs view: presence of any video setting (duration/frames/fps/video_width/
    video_height) means the workflow is an image2video run.
    """
    job_id = str(uuid.uuid4())
    is_video = any(
        kwargs.get(k) is not None
        for k in ("duration", "frames", "fps", "video_width", "video_height")
    )
    kind = "video" if is_video else "image"
    summary = _build_summary(workflow_name, prompt, kind)
    with jobs_lock:
        jobs[job_id] = {
            "status": "pending",
            "channel": _JobChannel(),
            "images": [],
            "assets": [],
            "cancel": threading.Event(),
            "server": server_address,
            "prompt_id": None,
            "kind": kind,
            "workflow_name": workflow_name,
            "prompt": prompt,
            "summary": summary,
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
        }
        _evict_old_jobs_locked()

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
        channel = jobs[job_id]["channel"]
        cancel_event = jobs[job_id]["cancel"]
        session = jobs[job_id]["session"]
        jobs[job_id]["status"] = "running"

    def send(msg_type, **kwargs):
        channel.send(json.dumps({"type": msg_type, **kwargs}))

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
            _mark_terminal_locked(job_id, "done")
        send("done", prompts=out, video=video)

    except JobCancelled:
        with jobs_lock:
            _mark_terminal_locked(job_id, "cancelled")
        send("cancelled", message="Cancelled")
    except GrokError as e:
        # A cancel closes the session, which surfaces as a GrokError from the
        # aborted request — report it as a cancellation, not an error.
        if cancel_event.is_set():
            with jobs_lock:
                _mark_terminal_locked(job_id, "cancelled")
            send("cancelled", message="Cancelled")
        else:
            with jobs_lock:
                _mark_terminal_locked(job_id, "error", error=str(e))
            send("error", message=str(e))
    except Exception as e:
        with jobs_lock:
            _mark_terminal_locked(job_id, "error", error=str(e))
        send("error", message=str(e))
    finally:
        channel.close()
        try:
            session.close()
        except Exception:
            pass


def start_sequence_job(master, count, replacements, video):
    """Create a tracked Grok prompt-sequence job; return its job_id."""
    job_id = str(uuid.uuid4())
    summary = _build_summary(None, master, "video-sequence" if video else "sequence")
    with jobs_lock:
        jobs[job_id] = {
            "status": "pending",
            "channel": _JobChannel(),
            "images": [],
            "assets": [],
            "cancel": threading.Event(),
            "server": None,
            "prompt_id": None,
            "session": requests.Session(),
            "kind": "sequence",
            "workflow_name": None,
            "prompt": master,
            "summary": summary,
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
        }
        _evict_old_jobs_locked()

    t = threading.Thread(
        target=run_sequence,
        args=(job_id, master, count, replacements, video),
        daemon=True,
    )
    t.start()
    return job_id
