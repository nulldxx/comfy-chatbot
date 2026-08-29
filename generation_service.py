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

from config import (COMFY_GENERATION_DIR, IMAGES_DIR, AUTO_PURGE_SECONDS,
                    COMFY_WS_PROGRESS)
from ComfyServer import ComfyServer, JobCancelled, JobRetry
from comfy_progress import ProgressListener, node_titles_for, node_weights_for
from catalogue import parse_loras_from_prompt, resolve_workflow_path
from persistence import append_session_image, append_session_note, rename_session
from seed_store import record_seeds
from grok import GrokError, generate_prompt_sequence, generate_video_prompt_sequence
from workflow import (
    LORA_PLACEHOLDER_RE,
    apply_placeholders, find_placeholders, fill_lora_sentinels,
    strip_lora_nodes, strip_last_frame_guide, randomize_seeds, apply_seed,
    collect_seeds, lora_path_for_os,
    apply_resolution, apply_steps,
    bypass_optimisation_nodes,
    reference_sentinel, strip_reference_nodes,
    reference_marker, find_marked_node, drop_node_output_links,
    node_link_output_indices,
    select_model_variant,
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

# The seed used by the most recent primary generation (t2i / i2v / t2v). /getseed
# reads it via get_last_seed() so the next of those runs can reproduce it. None
# until the first such generation completes its seed step; not persisted.
_last_seed: int | None = None
_last_seed_lock = threading.Lock()


def get_last_seed() -> int | None:
    """Return the seed of the most recent tracked generation, or None."""
    with _last_seed_lock:
        return _last_seed


def set_last_seed(seed: int) -> None:
    """Record the seed of a tracked generation for later reuse via /getseed."""
    global _last_seed
    with _last_seed_lock:
        _last_seed = seed

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

def _apply_track_drop(workflow, drop, server, send):
    """Disconnect the unwanted track of a single-node reference video loader.

    ``drop`` is one entry built by _run_generation_core: the marker substituted for
    <REFERENCE_VIDEO_n>, the real uploaded filename, and which of the two tracks the
    user ticked. We find the marked node, restore the filename into it, then delete the
    consumer links coming out of the track that wasn't wanted — the node itself stays,
    because it still has to load the clip for the track that was.

    Which output index carries which medium comes from the server's declared output
    types; if that lookup fails we fall back to classifying by the consumer's input
    name. If neither can tell them apart we raise rather than submit a graph that does
    the opposite of what was ticked (an invisible failure the user only sees after
    paying for a render).
    """
    node_id, key = find_marked_node(workflow, drop["marker"])
    if node_id is None:
        return
    workflow[node_id]["inputs"][key] = drop["filename"]
    class_type = workflow[node_id].get("class_type", "?")
    dropping = "audio" if not drop["want_audio"] else "video"

    outputs = []
    try:
        outputs = server.get_node_output_types(class_type)
    except Exception as e:                                    # transport/parse failure
        print(f"[track-drop] /object_info lookup for {class_type} failed: {e}")

    if outputs:
        audio_idx = [i for i, t in enumerate(outputs) if str(t).upper() == "AUDIO"]
        other_idx = [i for i, t in enumerate(outputs) if str(t).upper() != "AUDIO"]
    else:
        audio_idx, other_idx = node_link_output_indices(workflow, node_id)
        # Inconclusive: the node drives links, but every one of them landed on the
        # side we mean to keep, so we can't tell which carries the track to remove.
        if (audio_idx or other_idx) and not (audio_idx if dropping == "audio" else other_idx):
            raise ValueError(
                f"Can't honour the {dropping}-track setting for reference video "
                f"{drop['slot']}: node class {class_type} declares no AUDIO output and "
                f"its consumer inputs aren't named distinguishably. Re-tick both boxes, "
                f"or use a template with a separate "
                f"<REFERENCE_VIDEO_AUDIO_{drop['slot']}> loader."
            )

    removed = drop_node_output_links(
        workflow, node_id, audio_idx if dropping == "audio" else other_idx)
    if removed:
        send("progress",
             message=f"Reference video {drop['slot']}: {dropping} track disconnected")


def _run_generation_core(job_id, channel, cancel_event, prompt, loras,
                         server_address, server_os, workflow_name,
                         width=None, height=None, steps=None, denoise=None, workflow_dir=None,
                         input_image=None, input_mask=None, input_last_frame=None,
                         input_reference_images=None, input_reference_videos=None,
                         input_reference_video_audios=None, input_reference_audios=None,
                         preserve_mtime_from=None,
                         cleanup_input_image=False, duration=None, frames=None, fps=None,
                         video_width=None, video_height=None, retry_event=None,
                         seed=None, track_seed=False, disabled_optimizations=None):
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

    # Set once the workflow is submitted (see below). The closure below reads it
    # at call time, so declaring it here is enough — poll_status's 2s heartbeat
    # becomes the emission cadence for the progress numbers, which is why this
    # adds no SSE events beyond the ticks already being sent.
    listener = None

    def progress(msg_str):
        if msg_str == ".":
            snap = listener.latest() if listener is not None else None
            channel.send(json.dumps({"type": "tick", **(snap or {})}))
        else:
            send("progress", message=msg_str)

    purge_generation_started(server_address)
    try:
        # Resolve and confine to the workflow dir: workflow_name is client-supplied (and
        # may name a subfolder), so a "../" can't be allowed to escape it. It may also
        # carry an "@model" suffix naming one of the template's alternate models, which
        # is split off here and applied to the parsed graph further down.
        base_dir = workflow_dir or COMFY_GENERATION_DIR
        workflow_path, model_variant = resolve_workflow_path(base_dir, workflow_name)

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

        # Reference assets (the /references table, MiniMax H3 R2V). Up to 9 images,
        # 3 videos (each contributing its video and/or audio track) and 3 standalone
        # audios — all indexed. Each token is filled only when the template actually
        # contains it, so unrelated workflows are unaffected.
        #   <REFERENCE_IMAGE_1>  — image slot 1 with the LTX face-ID FALLBACK: uses the
        #       pinned image when supplied, else falls back to the triggered source image
        #       (its first frame) so those workflows stay usable without an explicit
        #       reference; a text2video run (no source image) with neither raises a clear
        #       error. This is the mandatory primary reference.
        #   <REFERENCE_IMAGE_2..9>, <REFERENCE_VIDEO_1..3>, <REFERENCE_AUDIO_1..3>
        #       — OPTIONAL slots: uploaded when supplied, else sentinel-filled and their
        #       loader nodes stripped after JSON parse (an unfilled non-LoRA placeholder
        #       is otherwise a hard error), so a MiniMax graph can run on any subset of
        #       references.
        #   <REFERENCE_VIDEO_AUDIO_1..3>  — the AUDIO TRACK of reference video n, never
        #       a separately uploaded file. Only meaningful in the two-node convention
        #       (see the video loop below); input_reference_video_audios[i] is normally
        #       the very same Path as input_reference_videos[i].
        def _padded(seq, n):
            out = list(seq or [])
            return out[:n] + [None] * (n - len(out))

        ref_images = _padded(input_reference_images, 9)
        ref_videos = _padded(input_reference_videos, 3)
        ref_video_audios = _padded(input_reference_video_audios, 3)
        ref_audios = _padded(input_reference_audios, 3)

        ref_sentinels = set()
        ref_upload_cache = {}

        def _upload_ref(path, label):
            key = str(path)
            if key not in ref_upload_cache:
                send("progress", message=f"Uploading {label} to ComfyUI...")
                ref_upload_cache[key] = server.upload_media(path)
            return ref_upload_cache[key]

        def _fill_reference(token, path, label, *, image1=False):
            if f"<{token}>" not in template:
                return
            if path is not None:
                mapping[token] = _upload_ref(path, label)
            elif image1:
                fallback = mapping.get("INPUT_IMAGE")
                if not fallback:
                    raise ValueError(
                        f"This workflow needs a <{token}> — add one with /references"
                    )
                mapping[token] = fallback
            else:
                sentinel = reference_sentinel(token)
                mapping[token] = sentinel
                ref_sentinels.add(sentinel)

        _fill_reference("REFERENCE_IMAGE_1", ref_images[0], "reference image 1", image1=True)
        for i in range(2, 10):
            _fill_reference(f"REFERENCE_IMAGE_{i}", ref_images[i - 1], f"reference image {i}")
        # Reference videos carry two tracks, chosen per slot by the /references
        # checkboxes. The two kwargs mean "the file feeding the video track" and "the
        # file feeding the audio track" — when both are on they are the SAME Path, so
        # ref_upload_cache uploads the clip to ComfyUI only once.
        #
        # Two template conventions are supported:
        #   two-node — the template also carries <REFERENCE_VIDEO_AUDIO_n>, on a second
        #       loader pointed at the same clip. Each token stands alone, so the plain
        #       sentinel + strip_reference_nodes path handles an unwanted track.
        #   one-node (the documented convention) — a single VHS Load Video node holds
        #       <REFERENCE_VIDEO_n> and drives both an IMAGE and an AUDIO consumer
        #       input. The node must load the file whenever EITHER track is wanted, so
        #       an unwanted track is removed by dropping that output's links after the
        #       graph is parsed (see track_drops below), not by removing the node.
        track_drops = []
        for i in range(1, 4):
            vtok, atok = f"REFERENCE_VIDEO_{i}", f"REFERENCE_VIDEO_AUDIO_{i}"
            vpath, apath = ref_videos[i - 1], ref_video_audios[i - 1]
            if f"<{atok}>" in template or (vpath is None and apath is None):
                _fill_reference(vtok, vpath, f"reference video {i}")
                _fill_reference(atok, apath, f"reference video {i} audio track")
                continue
            _fill_reference(vtok, vpath or apath, f"reference video {i}")
            if vpath is not None and apath is not None:
                continue
            # Exactly one track wanted: mark the node so we can find it unambiguously
            # after parsing, then write the uploaded filename back into it.
            uploaded = mapping[vtok]
            marker = reference_marker(vtok)
            mapping[vtok] = marker
            track_drops.append({
                "slot": i, "marker": marker, "filename": uploaded,
                "want_video": vpath is not None, "want_audio": apath is not None,
            })
        for i in range(1, 4):
            _fill_reference(f"REFERENCE_AUDIO_{i}", ref_audios[i - 1], f"reference audio {i}")

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

        if ref_sentinels:
            workflow, removed_refs = strip_reference_nodes(workflow, ref_sentinels)
            if removed_refs:
                send("progress", message=f"Skipping {len(removed_refs)} unused reference node(s)")

        # Single-node video tracks: disconnect the track the user unticked. Runs AFTER
        # strip_reference_nodes so an inactive slot's loader is already gone.
        for drop in track_drops:
            _apply_track_drop(workflow, drop, server, send)

        # Optimisation bypasses from /video-settings: drop the "[opt:<key>]" marked
        # nodes the user switched off, rewiring the model chain around them. Kept with
        # the other node surgery, i.e. before the UI->API conversion below.
        if disabled_optimizations:
            workflow, bypassed = bypass_optimisation_nodes(workflow, disabled_optimizations)
            if bypassed:
                send("progress", message=f"Bypassing optimisation(s): {', '.join(sorted(bypassed))}")

        if "nodes" in workflow:
            send("progress", message="Converting UI-format workflow to API format...")
            workflow = server.convert_ui_to_api_format(workflow)

        # Alternate models: a loader may name several interchangeable checkpoints as a
        # comma-separated list, one of which the "@model" suffix picked. Run
        # unconditionally so the list is always collapsed to a single name — ComfyUI
        # would choke on the comma. Deliberately AFTER the UI->API conversion, unlike the
        # node surgery above: this only rewrites an input string, so nothing depends on
        # the ordering, and running it here means it works for UI-format templates too.
        chosen = select_model_variant(workflow, model_variant)
        if chosen and model_variant:
            send("progress", message=f"Model: {chosen[0]}")

        if width and height:
            apply_resolution(workflow, width, height)
            send("progress", message=f"Resolution set to {width}×{height}")

        if steps is not None:
            apply_steps(workflow, steps)
            send("progress", message=f"Steps set to {steps}")

        # Seed handling: reuse a pinned seed (from /getseed, or the right-click
        # "Copy seed" menu item) if one was passed, otherwise randomize as usual.
        if seed is not None:
            if apply_seed(workflow, seed):
                send("progress", message=f"Reusing seed {seed}")
        elif randomize_seeds(workflow):
            send("progress", message="Randomized seed values")

        # Capture the effective seed. Read unconditionally — every job kind records
        # it against its own output files below (seed_store), while only primary
        # generations (t2i / i2v / t2v, track_seed=True) update the single global
        # that /getseed reads. Empty for a workflow with no seed input.
        used = collect_seeds(workflow)
        effective_seed = used[0] if used else None
        if track_seed and effective_seed is not None:
            set_last_seed(effective_seed)

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

        # Start reading ComfyUI's progress feed *before* submitting: it buffers
        # nothing, so a socket opened after the POST misses the opening messages.
        # Pure telemetry — a listener that can't connect just leaves the client's
        # progress bar indeterminate, exactly as before this existed.
        if COMFY_WS_PROGRESS:
            try:
                listener = ProgressListener(server_address, server.client_id,
                                            node_titles_for(workflow), len(workflow),
                                            node_weights_for(workflow))
                listener.start()
            except Exception as e:                    # never cost the user an image
                print(f"[comfy-progress] listener unavailable: {e}")
                listener = None

        try:
            send("progress", message=f"Submitting to {server_address}...")
            prompt_id = server.submit_workflow(workflow)
            with jobs_lock:
                jobs[job_id]["prompt_id"] = prompt_id
            if listener is not None:
                listener.bind(prompt_id)
            send("progress", message=f"Queued (ID: {prompt_id[:8]}…) — generating")

            prompt_data = server.poll_status(prompt_id, COMFY_POLL_TIMEOUT_SECONDS, progress,
                                             cancel_event=cancel_event, retry_event=retry_event)
        finally:
            # Cancel, retry, timeout and success all land here, so the reader
            # thread can't outlive the poll it was feeding.
            if listener is not None:
                listener.stop()
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

        # Remember which seed made these files, for the right-click "Copy seed"
        # menu item. Best-effort: a failure here must never fail the generation.
        record_seeds([p.name for p in dest_paths], effective_seed)

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


def run_face_detail_super(job_id, prompt, loras, server_address, server_os,
                          workflow_name, count, **kwargs):
    """Run the face-detailer ``count`` times over the same source image.

    Each pass calls _run_generation_core with identical inputs; the only thing
    that varies is the seed (randomize_seeds gives every submission a fresh one),
    so the result is ``count`` variations of the same detailed face. All the URLs
    are collected and returned together in the terminal ``done`` event, where the
    client renders them as a tile picker. Mirrors run_generation's lifecycle
    ownership but loops instead of running once.
    """
    with jobs_lock:
        channel = jobs[job_id]["channel"]
        cancel_event = jobs[job_id]["cancel"]
        jobs[job_id]["status"] = "running"

    def send(msg_type, **kwargs2):
        channel.send(json.dumps({"type": msg_type, **kwargs2}))

    all_urls = []
    try:
        for i in range(1, count + 1):
            if cancel_event.is_set():
                raise JobCancelled()
            send("progress", message=f"Detail {i}/{count}…")
            urls = _run_generation_core(
                job_id, channel, cancel_event, prompt, loras,
                server_address, server_os, workflow_name, **kwargs,
            )
            all_urls.extend(urls)
        with jobs_lock:
            _mark_terminal_locked(job_id, "done", images=all_urls, assets=all_urls)
        send("done", images=all_urls)
    except JobCancelled:
        with jobs_lock:
            _mark_terminal_locked(job_id, "cancelled", images=all_urls, assets=all_urls)
        send("cancelled", message="Cancelled")
    except Exception as e:
        with jobs_lock:
            _mark_terminal_locked(job_id, "error", error=str(e), images=all_urls, assets=all_urls)
        send("error", message=str(e))
    finally:
        channel.close()


def start_face_detail_super_job(prompt, loras, server_address, server_os,
                                workflow_name, count, **kwargs):
    """Create a tracked N-variation face-detail job; return its job_id.

    Sibling of start_generation_job for the /face-detail-super path: the record
    is identical (an image job) but the thread runs run_face_detail_super, which
    loops ``count`` times and returns all the variations at once.
    """
    job_id = str(uuid.uuid4())
    summary = _build_summary(workflow_name, prompt, "image")
    with jobs_lock:
        jobs[job_id] = {
            "status": "pending",
            "channel": _JobChannel(),
            "images": [],
            "assets": [],
            "cancel": threading.Event(),
            "server": server_address,
            "prompt_id": None,
            "kind": "image",
            "workflow_name": workflow_name,
            "prompt": prompt,
            "summary": summary,
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
        }
        _evict_old_jobs_locked()

    t = threading.Thread(
        target=run_face_detail_super,
        args=(job_id, prompt, loras, server_address, server_os, workflow_name, count),
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


# ---------------------------------------------------------------------------
# Server-side sequence runs
# ---------------------------------------------------------------------------
#
# A sequence *run* drives the whole loop server-side in one job: expand the
# master prompt via Grok, then generate each image sequentially on this thread
# via _run_generation_core, appending every finished image to the recording
# chat file. Because the loop and the persistence live on the server, a run
# keeps going — and stays recoverable via /chats — after the browser
# disconnects. A connected browser watches the same job over SSE and sees each
# image arrive through an "image" event.

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
