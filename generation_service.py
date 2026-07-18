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
from ComfyServer import ComfyServer, JobCancelled, JobRetry
from catalogue import parse_loras_from_prompt
from persistence import append_session_image, append_session_note, rename_session
from grok import GrokError, generate_prompt_sequence, generate_video_prompt_sequence
from workflow import (
    LORA_PLACEHOLDER_RE,
    apply_placeholders, find_placeholders, fill_lora_sentinels,
    strip_lora_nodes, strip_last_frame_guide, randomize_seeds, lora_path_for_os,
    apply_resolution, apply_steps,
)

# In-memory job tracking. Each job record carries:
#   status:          "pending" | "running" | "done" | "error" | "cancelled"
#   kind:            "image" | "video" | "sequence" | "sequence-run" | "task"
#   workflow_name:   filename of the workflow template (None for sequence jobs)
#   recording_name:  chat file a sequence-run appends images to (that kind only)
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

def _run_generation_core(job_id, channel, cancel_event, prompt, loras,
                         server_address, server_os, workflow_name,
                         width=None, height=None, steps=None, denoise=None, workflow_dir=None,
                         input_image=None, input_mask=None, input_last_frame=None,
                         preserve_mtime_from=None,
                         cleanup_input_image=False, duration=None, frames=None, fps=None,
                         video_width=None, video_height=None, retry_event=None):
    """Core generation pipeline shared by run_generation and run_sequence_run.

    Runs everything from placeholder substitution through downloading the output,
    emitting progress on the given ``channel`` and honouring ``cancel_event``, and
    returns the list of ``/images/...`` URLs. It writes ``jobs[job_id]["prompt_id"]``
    (so /api/cancel can interrupt the in-flight ComfyUI job) and brackets the
    auto-purge counters, but it does NOT set terminal job status or close the
    channel — the caller owns the job lifecycle. Raises JobCancelled on
    cancellation and other exceptions on failure.
    """
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
            lora_name = name if name.endswith('.safetensors') else f"{name}.safetensors"
            mapping[f"LORA_{i}_NAME"] = lora_path_for_os(lora_name, server_os)
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
        # LTXVAddGuide node (frame_idx=-1) that conditions the model on an end frame.
        # When a last frame is supplied we upload it and set strength 1.0; when absent
        # we strip the entire guide chain from the graph instead of relying on strength=0.0,
        # because LTXVAddGuide at zero still embeds the guide image into the latent at
        # the last position, which causes a snap-back transition at the end of the video.
        strip_guide = False
        if "<INPUT_LAST_FRAME>" in template:
            if input_last_frame is not None:
                send("progress", message="Uploading last frame to ComfyUI...")
                mapping["INPUT_LAST_FRAME"] = server.upload_image(input_last_frame)
                mapping["LAST_FRAME_STRENGTH"] = 1.0
            else:
                # Dummy values so the template parses as valid JSON; nodes removed below.
                mapping["INPUT_LAST_FRAME"] = mapping.get("INPUT_IMAGE", "")
                mapping["LAST_FRAME_STRENGTH"] = 0.0
                strip_guide = True

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

        if strip_guide:
            strip_last_frame_guide(workflow)
            send("progress", message="Last-frame guide stripped (no end frame)")

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

        prompt_data = server.poll_status(prompt_id, COMFY_POLL_TIMEOUT_SECONDS, progress,
                                         cancel_event=cancel_event, retry_event=retry_event)
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

        return image_urls
    finally:
        purge_generation_finished(server_address)


def run_generation(job_id, prompt, loras, server_address, server_os, workflow_name, **kwargs):
    """Run one generation as its own tracked job.

    Thin wrapper over _run_generation_core: reads the job's channel/cancel from the
    record, runs the core pipeline, and owns the terminal lifecycle (mark
    done/cancelled/error, close the channel). External behaviour is unchanged.
    """
    with jobs_lock:
        channel = jobs[job_id]["channel"]
        cancel_event = jobs[job_id]["cancel"]
        jobs[job_id]["status"] = "running"

    def send(msg_type, **kwargs2):
        channel.send(json.dumps({"type": msg_type, **kwargs2}))

    try:
        image_urls = _run_generation_core(
            job_id, channel, cancel_event, prompt, loras,
            server_address, server_os, workflow_name, **kwargs,
        )
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


# ---------------------------------------------------------------------------
# Server-side sequence runs
# ---------------------------------------------------------------------------
#
# Unlike start_sequence_job (which only expands the master prompt via Grok and
# hands the prompt list back to the browser to generate one-by-one), a sequence
# *run* drives the whole loop server-side in one job: expand, then generate each
# image sequentially on this thread via _run_generation_core, appending every
# finished image to the recording chat file. Because the loop and the
# persistence live on the server, a run keeps going — and stays recoverable via
# /chats — after the browser disconnects. A connected browser watches the
# same job over SSE and sees each image arrive through an "image" event.

def run_sequence_run(job_id, master, count, replacements, video, gen_settings):
    with jobs_lock:
        channel = jobs[job_id]["channel"]
        cancel_event = jobs[job_id]["cancel"]
        retry_event = jobs[job_id]["retry"]
        session = jobs[job_id]["session"]
        jobs[job_id]["status"] = "running"

    def send(msg_type, **kwargs):
        channel.send(json.dumps({"type": msg_type, **kwargs}))

    all_urls = []
    failed = []
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

        # Let a connected browser render the plan (also drives /sequence-review).
        send("prompts", prompts=out, video=video)

        extra_prompt = (gen_settings.get("extraPrompt") or "").strip()
        total = len(out)
        for i, item in enumerate(out, start=1):
            if cancel_event.is_set():
                raise JobCancelled()

            if video:
                item_prompt = item.get("prompt", "")
                video_meta = {"action": item.get("action", ""), "audio": item.get("audio", "")}
            else:
                item_prompt = item
                video_meta = None
            if not item_prompt:
                continue

            clean_prompt, loras = parse_loras_from_prompt(item_prompt)
            if not clean_prompt:
                send("progress", message=f"Shot {i}/{total}: empty after LoRA tags, skipping")
                continue
            # extraPrompt is appended for generation only, matching the client's
            # old runGeneration behaviour — the stored/displayed prompt (used for
            # append_image_to_recording and the "image" event below) stays the
            # original item_prompt, without the suffix.
            gen_prompt = f"{clean_prompt} {extra_prompt}".strip() if extra_prompt else clean_prompt

            # Announce the start of this shot so the attached client can open a
            # fresh per-shot bubble (with its own status line, retry/cancel buttons
            # and generation timer) before the "Generating…"/"Queued…" progress and
            # the final image arrive — restoring the per-image UX the old
            # client-driven loop had. Carries the original prompt (pre-extra) and
            # video meta so the bubble's user line matches the "image" event.
            # Emitted once, before the retry loop, so the client keeps the same
            # per-shot bubble across any retries.
            send("shot", index=i, total=total, prompt=item_prompt, videoMeta=video_meta)

            # Per-shot retry loop. The user can abort a stuck/failed generation
            # (via /api/retry-shot, which trips retry_event) to re-run this same
            # prompt without losing completed shots or the remaining queue. A
            # failed attempt pauses here — waiting for a retry or a whole-run
            # cancel — rather than advancing, so the user stays in control.
            urls = None
            while urls is None:
                if cancel_event.is_set():
                    raise JobCancelled()
                retry_event.clear()
                send("progress", message=f"Generating {i}/{total}…")
                try:
                    urls = _run_generation_core(
                        job_id, channel, cancel_event, gen_prompt, loras,
                        gen_settings["server"], gen_settings["server_os"], gen_settings["workflow"],
                        workflow_dir=COMFY_GENERATION_DIR,
                        width=gen_settings.get("width"),
                        height=gen_settings.get("height"),
                        steps=gen_settings.get("steps"),
                        retry_event=retry_event,
                    )
                except JobCancelled:
                    raise
                except JobRetry:
                    # User asked to re-run this shot; loop and try again.
                    send("progress", message=f"Retrying {i}/{total}…")
                    continue
                except Exception as e:
                    # This shot failed. Persist and surface it, then pause on the
                    # shot until the user retries (retry_event) or cancels the
                    # whole run (cancel_event) — the sequence does not advance.
                    failed.append({"index": i, "prompt": item_prompt, "error": str(e)})
                    try:
                        append_failure_to_recording(job_id, item_prompt, str(e))
                    except Exception:
                        pass
                    send("shot_failed", prompt=item_prompt, error=str(e), index=i, total=total)
                    while not retry_event.is_set():
                        if cancel_event.is_set():
                            raise JobCancelled()
                        time.sleep(0.25)
                    # Retry requested: drop this failure from the record (it will
                    # be re-attempted) and loop.
                    failed[:] = [f for f in failed if f.get("index") != i]
                    continue

            for url in urls:
                all_urls.append(url)
                try:
                    append_image_to_recording(
                        job_id, url, item_prompt, video_meta, gen_settings
                    )
                except Exception as e:
                    send("progress", message=f"Warning: could not persist to session: {e}")
                send("image", url=url, prompt=item_prompt, videoMeta=video_meta,
                     index=i, total=total)

        with jobs_lock:
            _mark_terminal_locked(job_id, "done", images=all_urls, assets=all_urls, failed=failed)
        send("done", images=all_urls, prompts=out, video=video, failed=failed)

    except JobCancelled:
        with jobs_lock:
            _mark_terminal_locked(job_id, "cancelled", images=all_urls, assets=all_urls, failed=failed)
        send("cancelled", message="Cancelled")
    except GrokError as e:
        # A cancel during the Grok call closes the session, surfacing as a
        # GrokError from the aborted request — report it as a cancellation.
        if cancel_event.is_set():
            with jobs_lock:
                _mark_terminal_locked(job_id, "cancelled", images=all_urls, assets=all_urls, failed=failed)
            send("cancelled", message="Cancelled")
        else:
            with jobs_lock:
                _mark_terminal_locked(job_id, "error", error=str(e), images=all_urls, assets=all_urls, failed=failed)
            send("error", message=str(e))
    except Exception as e:
        with jobs_lock:
            _mark_terminal_locked(job_id, "error", error=str(e), images=all_urls, assets=all_urls, failed=failed)
        send("error", message=str(e))
    finally:
        channel.close()
        try:
            session.close()
        except Exception:
            pass


def start_sequence_run_job(master, count, replacements, video, recording_name, gen_settings):
    """Create a tracked server-side sequence run; return its job_id.

    The record carries both a requests.Session (so /api/cancel can abort the Grok
    call) and, once generation starts, a prompt_id/server (so /api/cancel can
    interrupt the in-flight ComfyUI job). recording_name is the session file the
    run appends each image to; it may be retargeted mid-run by
    rename_and_retarget_session (see append_image_to_recording).
    """
    job_id = str(uuid.uuid4())
    summary = _build_summary(None, master, "video-sequence-run" if video else "sequence-run")
    with jobs_lock:
        jobs[job_id] = {
            "status": "pending",
            "channel": _JobChannel(),
            "images": [],
            "assets": [],
            "cancel": threading.Event(),
            "retry": threading.Event(),
            "server": gen_settings.get("server"),
            "prompt_id": None,
            "session": requests.Session(),
            "recording_name": recording_name,
            "kind": "sequence-run",
            "workflow_name": gen_settings.get("workflow"),
            "prompt": master,
            "summary": summary,
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
        }
        _evict_old_jobs_locked()

    t = threading.Thread(
        target=run_sequence_run,
        args=(job_id, master, count, replacements, video, gen_settings),
        daemon=True,
    )
    t.start()
    return job_id


def append_image_to_recording(job_id, url, prompt, video_meta, settings):
    """Append one image to whatever session this job is currently recording to.

    Reads jobs[job_id]["recording_name"] and performs the append to persistence
    in the SAME jobs_lock critical section as rename_and_retarget_session's file
    move + retarget, so the two can never interleave: a rename can't complete
    with a job's append landing on the just-vacated old filename (or vice versa).
    A no-op if the job has no recording_name (shouldn't normally happen).
    """
    with jobs_lock:
        rec = jobs.get(job_id)
        name = rec.get("recording_name") if rec else None
        if name:
            append_session_image(name, url, prompt, video_meta, settings=settings)


def append_failure_to_recording(job_id, prompt, error_text):
    """Record a failed shot (no image) against whatever session this job is
    recording to, under the same jobs_lock-guarded pattern as
    append_image_to_recording — see its docstring for why."""
    with jobs_lock:
        rec = jobs.get(job_id)
        name = rec.get("recording_name") if rec else None
        if name:
            append_session_note(name, prompt, f"⚠ Generation failed: {error_text}")


def rename_and_retarget_session(src, dst):
    """Rename a session file and repoint any live job recording to it — atomically.

    Called by /api/sessions/rename. Holds jobs_lock across the file rename itself
    (rename_session, which internally takes persistence's sessions_write_lock) so
    it can't interleave with append_image_to_recording/append_failure_to_recording,
    which read a job's recording_name and perform their persistence call under the
    same lock. This closes two bugs the naive "retarget then rename" ordering had:
    a live run's append landing on a filename mid-rename (TOCTOU), and a FAILED
    rename (destination already exists) still permanently repointing the job
    before the failure was known.

    Raises FileExistsError if dst already exists (no job is retargeted in that
    case — the exception propagates before the loop below runs). Raises
    FileNotFoundError if src has no file yet (a temp session with no images
    written) — any live job is still retargeted in that case, since there's
    nothing on disk to conflict with; the exception is only informational for
    the caller (a temp session with no file is a normal, harmless case to rename).
    """
    with jobs_lock:
        try:
            rename_session(src, dst)
            missing = False
        except FileNotFoundError:
            missing = True
        for rec in jobs.values():
            if rec.get("status") not in TERMINAL_STATUSES and rec.get("recording_name") == src:
                rec["recording_name"] = dst
    if missing:
        raise FileNotFoundError(src)


# ---------------------------------------------------------------------------
# Generic background jobs
# ---------------------------------------------------------------------------
#
# For maintenance operations (e.g. /fscheck) that are too slow for the request
# thread but aren't ComfyUI generations. Reuses the same _JobChannel + SSE
# plumbing, so /api/progress/<job_id> streams them unchanged. The job's "kind"
# keeps it out of the /api/jobs recovery view (which is image/video only).

def run_background_job(job_id, fn):
    with jobs_lock:
        channel = jobs[job_id]["channel"]
        jobs[job_id]["status"] = "running"

    def send(msg_type, **kwargs):
        channel.send(json.dumps({"type": msg_type, **kwargs}))

    try:
        result = fn(lambda message: send("progress", message=message))
        with jobs_lock:
            _mark_terminal_locked(job_id, "done")
        send("done", **(result or {}))
    except Exception as e:
        with jobs_lock:
            _mark_terminal_locked(job_id, "error", error=str(e))
        send("error", message=str(e))
    finally:
        channel.close()


def start_background_job(fn, kind="task", summary=None):
    """Run fn(emit) on a daemon thread as a tracked job; return its job_id.

    fn receives an ``emit(message)`` callable to push progress lines, and may
    return a dict whose keys are merged into the terminal ``done`` SSE event (so
    the client can render structured results). Any exception becomes an ``error``
    event. Streamable at /api/progress/<job_id> like any generation job.
    """
    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "status": "pending",
            "channel": _JobChannel(),
            "images": [],
            "assets": [],
            "cancel": threading.Event(),
            "server": None,
            "prompt_id": None,
            "kind": kind,
            "workflow_name": None,
            "prompt": "",
            "summary": summary or kind,
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
        }
        _evict_old_jobs_locked()

    t = threading.Thread(target=run_background_job, args=(job_id, fn), daemon=True)
    t.start()
    return job_id
