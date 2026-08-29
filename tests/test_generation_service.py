"""Tests for the generation job runners in generation_service.

These exercise the (mocked) Grok and ComfyUI calls inside tracked jobs and the
channel messages (done/cancelled/error) the SSE endpoint relays to the client.
The HTTP layer (grok._chat) is never touched."""
import os
import sys
import json
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import generation_service as gs
import seed_store
import shutil


def _drain(channel):
    """Collect all messages from a _JobChannel's event log."""
    return [json.loads(m) for m in channel.snapshot()]


class RunGenerationWrapperTests(unittest.TestCase):
    """run_generation is now a thin wrapper over _run_generation_core; verify it
    still owns the terminal lifecycle (done/cancelled/error) correctly."""

    def _make_job(self):
        job_id = "test-gen-job"
        gs.jobs[job_id] = {
            "status": "pending",
            "channel": gs._JobChannel(),
            "images": [],
            "assets": [],
            "cancel": threading.Event(),
            "server": "http://s",
            "prompt_id": None,
            "kind": "image",
            "workflow_name": "wf",
            "prompt": "a cat",
            "summary": "test",
            "started_at": 0.0,
            "finished_at": None,
            "error": None,
        }
        return job_id

    def tearDown(self):
        gs.jobs.pop("test-gen-job", None)

    def test_wrapper_emits_done_with_images(self):
        job_id = self._make_job()
        with patch.object(gs, "_run_generation_core", return_value=["/images/a.png", "/images/b.png"]):
            gs.run_generation(job_id, "a cat", [], "http://s", "linux", "wf")
        done = [m for m in _drain(gs.jobs[job_id]["channel"]) if m["type"] == "done"][0]
        self.assertEqual(done["images"], ["/images/a.png", "/images/b.png"])
        self.assertEqual(gs.jobs[job_id]["status"], "done")
        self.assertEqual(gs.jobs[job_id]["assets"], ["/images/a.png", "/images/b.png"])

    def test_wrapper_cancelled(self):
        from ComfyServer import JobCancelled
        job_id = self._make_job()
        with patch.object(gs, "_run_generation_core", side_effect=JobCancelled()):
            gs.run_generation(job_id, "a cat", [], "http://s", "linux", "wf")
        self.assertEqual(gs.jobs[job_id]["status"], "cancelled")

    def test_wrapper_error(self):
        job_id = self._make_job()
        with patch.object(gs, "_run_generation_core", side_effect=ValueError("boom")):
            gs.run_generation(job_id, "a cat", [], "http://s", "linux", "wf")
        err = [m for m in _drain(gs.jobs[job_id]["channel"]) if m["type"] == "error"][0]
        self.assertEqual(err["message"], "boom")
        self.assertEqual(gs.jobs[job_id]["status"], "error")


class CoreSeedRecordingTests(unittest.TestCase):
    """_run_generation_core links each output file to the seed that made it.

    This is the only place the seed and the final filenames are both in scope, and
    the whole "Copy seed" menu item hangs off it, so the core is driven for real
    here (everything else in this file mocks it out) with a stub ComfyUI server.
    """

    TEMPLATE = json.dumps({
        "1": {"class_type": "KSampler",
              "inputs": {"seed": 0, "text": "<PROMPT>"}},
    })

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)
        self.images_dir = self.root / "images"
        self.images_dir.mkdir()
        self.wf_dir = self.root / "workflows"
        self.wf_dir.mkdir()
        (self.wf_dir / "t2i.json").write_text(self.TEMPLATE)

        self.submitted = {}

        outer = self

        class FakeServer:
            def __init__(self, address):
                # Real ComfyServer generates one per instance; the progress
                # listener subscribes to ComfyUI's /ws feed with it.
                self.client_id = "client-id-1234"

            def submit_workflow(self, workflow):
                outer.submitted["workflow"] = workflow
                return "prompt-id-1234"

            def poll_status(self, prompt_id, timeout=None, callback=None, **k):
                # The real poll loop emits a "." heartbeat every 2s, which is
                # what carries the progress snapshot to the client.
                if callback:
                    callback(".")
                return {"outputs": {}}

            def get_output_images(self, prompt_data):
                return ["out.png"]

            def download_images(self, images, dest_dir):
                paths = []
                for name in images:
                    p = Path(dest_dir) / name
                    p.write_bytes(b"\x89PNG\r\n\x1a\n")
                    paths.append(p)
                return paths

        self._patchers = [
            patch.object(gs, "ComfyServer", FakeServer),
            # Off by default: otherwise every generation test would open a real
            # socket at the fake server address. ProgressTickTests turns it back
            # on with a stub listener.
            patch.object(gs, "COMFY_WS_PROGRESS", False),
            patch.object(gs, "IMAGES_DIR", self.images_dir),
            patch.object(gs, "purge_generation_started", lambda *a, **k: None),
            patch.object(gs, "purge_generation_finished", lambda *a, **k: None),
            patch.object(seed_store, "IMAGES_DIR", self.images_dir),
            patch.object(seed_store, "SEEDS_FILE", self.images_dir / ".seeds.json"),
        ]
        for p in self._patchers:
            p.start()

        self.job_id = "job-1"
        self.channel = gs._JobChannel()
        gs.jobs[self.job_id] = {"prompt_id": None}

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        gs.jobs.pop(self.job_id, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, **kwargs):
        return gs._run_generation_core(
            self.job_id, self.channel, threading.Event(),
            "a cat", [], "127.0.0.1:8188", "unix", "t2i",
            workflow_dir=self.wf_dir, **kwargs,
        )

    def _recorded(self, urls):
        return [seed_store.get_seed(u.split("/")[-1]) for u in urls]

    def test_randomized_seed_is_recorded_against_the_output(self):
        urls = self._run()
        used = self.submitted["workflow"]["1"]["inputs"]["seed"]
        self.assertNotEqual(used, 0)  # actually randomized
        self.assertEqual(self._recorded(urls), [str(used)])

    def test_pinned_seed_is_applied_and_recorded(self):
        # The round trip the menu promises: copy a seed, generate, get it back.
        pinned = 2**64 - 1
        urls = self._run(seed=pinned)
        self.assertEqual(self.submitted["workflow"]["1"]["inputs"]["seed"], pinned)
        self.assertEqual(self._recorded(urls), [str(pinned)])

    def test_recorded_without_track_seed(self):
        # Every job kind records — face-detail, upscale, i2i, inpaint, remove and
        # sequence-run shots all pass track_seed=False but still produce a file.
        gs.set_last_seed(4242)
        urls = self._run(track_seed=False)
        self.assertIsNotNone(self._recorded(urls)[0])
        # ...while the /getseed global stays untouched, so its semantics are unchanged.
        self.assertEqual(gs.get_last_seed(), 4242)

    def test_track_seed_updates_the_getseed_global(self):
        urls = self._run(track_seed=True)
        self.assertEqual(str(gs.get_last_seed()), self._recorded(urls)[0])

    def test_workflow_without_a_seed_input_records_nothing(self):
        (self.wf_dir / "t2i.json").write_text(
            json.dumps({"1": {"class_type": "SaveImage", "inputs": {"text": "<PROMPT>"}}})
        )
        urls = self._run()
        self.assertEqual(self._recorded(urls), [None])

    def test_a_failed_seed_write_does_not_fail_the_generation(self):
        # Best-effort by design: losing a seed must never cost the user an image.
        with patch.object(seed_store, "atomic_write_json", side_effect=OSError("full")):
            urls = self._run()
        self.assertEqual(len(urls), 1)


class RunSequenceRunTests(unittest.TestCase):
    """run_sequence_run drives the whole sequence server-side: Grok expand, then
    generate each image via _run_generation_core, appending each to the session
    file and emitting an 'image' event. Grok and the generation core are mocked."""

    def _make_job(self, recording_name="run-sess"):
        job_id = "test-run-job"
        gs.jobs[job_id] = {
            "status": "pending",
            "channel": gs._JobChannel(),
            "images": [],
            "assets": [],
            "cancel": threading.Event(),
            "retry": threading.Event(),
            "server": "http://s",
            "prompt_id": None,
            "session": None,
            "recording_name": recording_name,
            "kind": "sequence-run",
            "workflow_name": "wf",
            "prompt": "x",
            "summary": "test",
            "started_at": 0.0,
            "finished_at": None,
            "error": None,
        }
        return job_id

    def tearDown(self):
        gs.jobs.pop("test-run-job", None)

    def _settings(self):
        return {"server": "http://s", "server_os": "linux", "workflow": "wf",
                "width": None, "height": None, "steps": None}

    def test_happy_path_appends_and_emits_image_per_prompt(self):
        job_id = self._make_job()
        appended = []

        def fake_core(jid, channel, cancel, prompt, loras, *a, **k):
            return [f"/images/{prompt.replace(' ', '_')}.png"]

        with patch.object(gs, "generate_prompt_sequence", return_value=["a cat", "a dog"]), \
             patch.object(gs, "_run_generation_core", side_effect=fake_core), \
             patch.object(gs, "append_session_image", side_effect=lambda *a, **k: appended.append(a)):
            gs.run_sequence_run(job_id, "x", 2, [], video=False, gen_settings=self._settings())

        msgs = _drain(gs.jobs[job_id]["channel"])
        images = [m for m in msgs if m["type"] == "image"]
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0]["url"], "/images/a_cat.png")
        self.assertEqual(images[0]["prompt"], "a cat")
        done = [m for m in msgs if m["type"] == "done"][0]
        self.assertEqual(done["images"], ["/images/a_cat.png", "/images/a_dog.png"])
        self.assertEqual(len(appended), 2)
        self.assertEqual(gs.jobs[job_id]["status"], "done")

    def test_prompts_event_precedes_images(self):
        job_id = self._make_job()
        with patch.object(gs, "generate_prompt_sequence", return_value=["a cat"]), \
             patch.object(gs, "_run_generation_core", return_value=["/images/a.png"]), \
             patch.object(gs, "append_session_image"):
            gs.run_sequence_run(job_id, "x", 1, [], video=False, gen_settings=self._settings())
        types = [m["type"] for m in _drain(gs.jobs[job_id]["channel"])]
        self.assertIn("prompts", types)
        self.assertLess(types.index("prompts"), types.index("image"))

    def test_shot_event_precedes_each_image_with_prompt(self):
        job_id = self._make_job()
        with patch.object(gs, "generate_prompt_sequence", return_value=["a cat", "a dog"]), \
             patch.object(gs, "_run_generation_core", side_effect=lambda jid, ch, c, p, l, *a, **k: [f"/images/{p.replace(' ', '_')}.png"]), \
             patch.object(gs, "append_session_image"):
            gs.run_sequence_run(job_id, "x", 2, [], video=False, gen_settings=self._settings())
        msgs = _drain(gs.jobs[job_id]["channel"])
        shots = [m for m in msgs if m["type"] == "shot"]
        self.assertEqual(len(shots), 2)
        self.assertEqual([s["prompt"] for s in shots], ["a cat", "a dog"])
        self.assertEqual([s["index"] for s in shots], [1, 2])
        self.assertTrue(all(s["total"] == 2 for s in shots))
        # Each shot event precedes its own image event.
        types = [m["type"] for m in msgs]
        first_shot = types.index("shot")
        first_image = types.index("image")
        self.assertLess(first_shot, first_image)

    def test_shot_event_carries_video_meta(self):
        job_id = self._make_job()
        shots = [{"prompt": "a cat", "action": "leaps", "audio": "meow"}]
        with patch.object(gs, "generate_video_prompt_sequence", return_value=shots), \
             patch.object(gs, "_run_generation_core", return_value=["/images/a.png"]), \
             patch.object(gs, "append_session_image"):
            gs.run_sequence_run(job_id, "x", 1, [], video=True, gen_settings=self._settings())
        shot = [m for m in _drain(gs.jobs[job_id]["channel"]) if m["type"] == "shot"][0]
        self.assertEqual(shot["videoMeta"], {"action": "leaps", "audio": "meow"})

    def test_per_shot_failure_pauses_then_retry_succeeds(self):
        # A failed shot no longer auto-advances: it pauses awaiting a retry (or a
        # whole-run cancel). Here the first attempt at "bad" fails and trips
        # retry_event (as /api/retry-shot would), so the shot is re-run and
        # succeeds. The retried failure is cleared from the terminal record.
        job_id = self._make_job()
        attempts = {"bad": 0}

        def core(jid, channel, cancel, prompt, loras, *a, **k):
            if prompt == "bad":
                attempts["bad"] += 1
                if attempts["bad"] == 1:
                    gs.jobs[job_id]["retry"].set()  # user hits retry while paused
                    raise ValueError("boom")
                return ["/images/recovered.png"]
            return ["/images/ok.png"]

        with patch.object(gs, "generate_prompt_sequence", return_value=["bad", "good"]), \
             patch.object(gs, "_run_generation_core", side_effect=core), \
             patch.object(gs, "append_session_image"), \
             patch.object(gs, "append_failure_to_recording"):
            gs.run_sequence_run(job_id, "x", 2, [], video=False, gen_settings=self._settings())

        msgs = _drain(gs.jobs[job_id]["channel"])
        images = [m for m in msgs if m["type"] == "image"]
        self.assertEqual(len(images), 2)  # recovered "bad" + "good"
        self.assertEqual(attempts["bad"], 2)  # failed once, retried once
        self.assertTrue(any(m["type"] == "shot_failed" for m in msgs))
        self.assertEqual(gs.jobs[job_id]["status"], "done")
        self.assertEqual(gs.jobs[job_id]["failed"], [])  # retried failure cleared

    def test_cancel_between_images_marks_cancelled(self):
        job_id = self._make_job()
        appended = []

        def core(jid, channel, cancel, prompt, loras, *a, **k):
            gs.jobs[job_id]["cancel"].set()
            return ["/images/a.png"]

        with patch.object(gs, "generate_prompt_sequence", return_value=["a", "b"]), \
             patch.object(gs, "_run_generation_core", side_effect=core), \
             patch.object(gs, "append_session_image", side_effect=lambda *a, **k: appended.append(a)):
            gs.run_sequence_run(job_id, "x", 2, [], video=False, gen_settings=self._settings())
        self.assertEqual(gs.jobs[job_id]["status"], "cancelled")
        self.assertEqual(len(appended), 1)

    def test_video_stores_video_meta(self):
        job_id = self._make_job()
        shots = [{"prompt": "a cat", "action": "leaps", "audio": "meow"}]
        metas = []

        def fake_append(name, url, prompt, video_meta=None, settings=None):
            metas.append(video_meta)

        with patch.object(gs, "generate_video_prompt_sequence", return_value=shots), \
             patch.object(gs, "_run_generation_core", return_value=["/images/a.png"]), \
             patch.object(gs, "append_session_image", side_effect=fake_append):
            gs.run_sequence_run(job_id, "x", 1, [], video=True, gen_settings=self._settings())
        self.assertEqual(metas, [{"action": "leaps", "audio": "meow"}])
        img = [m for m in _drain(gs.jobs[job_id]["channel"]) if m["type"] == "image"][0]
        self.assertEqual(img["videoMeta"], {"action": "leaps", "audio": "meow"})

    def test_recording_name_reread_each_iteration(self):
        # A mid-run rename (rename_and_retarget_session) must redirect later
        # appends. The name is re-read fresh each iteration (inside
        # append_image_to_recording, under jobs_lock), so a rename applied after
        # the first append is picked up for the second.
        job_id = self._make_job(recording_name="temp-1")
        names = []

        def fake_append(name, *a, **k):
            names.append(name)
            if name == "temp-1":
                gs.jobs[job_id]["recording_name"] = "renamed"

        with patch.object(gs, "generate_prompt_sequence", return_value=["first", "second"]), \
             patch.object(gs, "_run_generation_core", return_value=["/images/x.png"]), \
             patch.object(gs, "append_session_image", side_effect=fake_append):
            gs.run_sequence_run(job_id, "x", 2, [], video=False, gen_settings=self._settings())
        self.assertEqual(names, ["temp-1", "renamed"])

    def test_extra_prompt_applied_to_generation_not_to_stored_prompt(self):
        # extraPrompt is appended for generation only (matching the old client
        # runGeneration behaviour) — the persisted/displayed prompt (passed to
        # append_session_image and the "image" event) stays the original prompt.
        job_id = self._make_job()
        seen_prompts = []

        def fake_core(jid, channel, cancel, prompt, loras, *a, **k):
            seen_prompts.append(prompt)
            return ["/images/a.png"]

        settings = self._settings()
        settings["extraPrompt"] = "in the style of monet"
        with patch.object(gs, "generate_prompt_sequence", return_value=["a cat"]), \
             patch.object(gs, "_run_generation_core", side_effect=fake_core), \
             patch.object(gs, "append_session_image") as append_mock:
            gs.run_sequence_run(job_id, "x", 1, [], video=False, gen_settings=settings)
        self.assertEqual(seen_prompts, ["a cat in the style of monet"])
        append_mock.assert_called_once_with(
            "run-sess", "/images/a.png", "a cat", None, settings=settings
        )
        img = [m for m in _drain(gs.jobs[job_id]["channel"]) if m["type"] == "image"][0]
        self.assertEqual(img["prompt"], "a cat")

    def test_no_extra_prompt_leaves_prompt_unchanged(self):
        job_id = self._make_job()
        seen_prompts = []

        def fake_core(jid, channel, cancel, prompt, loras, *a, **k):
            seen_prompts.append(prompt)
            return ["/images/a.png"]

        with patch.object(gs, "generate_prompt_sequence", return_value=["a cat"]), \
             patch.object(gs, "_run_generation_core", side_effect=fake_core), \
             patch.object(gs, "append_session_image"):
            gs.run_sequence_run(job_id, "x", 1, [], video=False, gen_settings=self._settings())
        self.assertEqual(seen_prompts, ["a cat"])

    def test_per_shot_failure_persisted_and_emitted(self):
        # A failed shot is (1) sent as a distinct "shot_failed" SSE event (not
        # just a transient progress line), (2) persisted to the session via
        # append_failure_to_recording so it's visible after a later
        # /session-load, and (3) retained in the terminal "failed" list when the
        # run is cancelled while paused on it (a retry would instead clear it).
        job_id = self._make_job()
        persisted = []

        def core(jid, channel, cancel, prompt, loras, *a, **k):
            if prompt == "bad":
                gs.jobs[job_id]["cancel"].set()  # user cancels the run while paused
                raise ValueError("boom")
            return ["/images/ok.png"]

        with patch.object(gs, "generate_prompt_sequence", return_value=["bad", "good"]), \
             patch.object(gs, "_run_generation_core", side_effect=core), \
             patch.object(gs, "append_session_image"), \
             patch.object(gs, "append_failure_to_recording",
                          side_effect=lambda *a, **k: persisted.append(a)):
            gs.run_sequence_run(job_id, "x", 2, [], video=False, gen_settings=self._settings())

        msgs = _drain(gs.jobs[job_id]["channel"])
        failed_events = [m for m in msgs if m["type"] == "shot_failed"]
        self.assertEqual(len(failed_events), 1)
        self.assertEqual(failed_events[0]["prompt"], "bad")
        self.assertEqual(failed_events[0]["error"], "boom")

        self.assertEqual(gs.jobs[job_id]["status"], "cancelled")
        self.assertEqual(gs.jobs[job_id]["failed"][0]["prompt"], "bad")

        # append_failure_to_recording(job_id, prompt, error_text)
        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0][1], "bad")


class AppendToRecordingTests(unittest.TestCase):
    """append_image_to_recording / append_failure_to_recording read a job's
    current recording_name and perform the persistence call in one jobs_lock
    critical section, so they can't interleave with rename_and_retarget_session."""

    def _make_job(self, recording_name="sess"):
        job_id = "test-append-job"
        gs.jobs[job_id] = {
            "status": "running", "channel": gs._JobChannel(), "images": [], "assets": [],
            "cancel": threading.Event(), "server": None, "prompt_id": None,
            "recording_name": recording_name, "kind": "sequence-run",
            "workflow_name": None, "prompt": "x", "summary": "test",
            "started_at": 0.0, "finished_at": None, "error": None,
        }
        return job_id

    def tearDown(self):
        gs.jobs.pop("test-append-job", None)

    def test_append_image_uses_current_recording_name(self):
        job_id = self._make_job("sess-a")
        with patch.object(gs, "append_session_image") as m:
            gs.append_image_to_recording(job_id, "/images/a.png", "a cat", None, {"workflow": "wf"})
        m.assert_called_once_with("sess-a", "/images/a.png", "a cat", None, settings={"workflow": "wf"})

    def test_append_image_noop_without_recording_name(self):
        job_id = self._make_job(recording_name=None)
        with patch.object(gs, "append_session_image") as m:
            gs.append_image_to_recording(job_id, "/images/a.png", "a cat", None, {})
        m.assert_not_called()

    def test_append_failure_uses_current_recording_name(self):
        job_id = self._make_job("sess-a")
        with patch.object(gs, "append_session_note") as m:
            gs.append_failure_to_recording(job_id, "a cat", "boom")
        m.assert_called_once_with("sess-a", "a cat", "⚠ Generation failed: boom")


class RenameAndRetargetSessionTests(unittest.TestCase):
    """rename_and_retarget_session (generation_service.py) holds jobs_lock across
    both the file rename (persistence.rename_session) and the job-record retarget,
    so a live run's append_image_to_recording (which also reads recording_name
    under jobs_lock) can never observe a stale name mid-rename, and a FAILED
    rename never leaves a job silently repointed at the wrong session."""

    def _make_job(self, job_id, recording_name, status="running"):
        gs.jobs[job_id] = {
            "status": status, "channel": gs._JobChannel(), "images": [], "assets": [],
            "cancel": threading.Event(), "server": None, "prompt_id": None,
            "recording_name": recording_name, "kind": "sequence-run",
            "workflow_name": None, "prompt": "x", "summary": "test",
            "started_at": 0.0, "finished_at": None, "error": None,
        }

    def tearDown(self):
        for jid in list(gs.jobs):
            if jid.startswith("test-rename-job"):
                gs.jobs.pop(jid, None)

    def test_success_retargets_live_job(self):
        self._make_job("test-rename-job", "temp-1")
        with patch.object(gs, "rename_session", return_value="dst") as m:
            gs.rename_and_retarget_session("temp-1", "dst")
        m.assert_called_once_with("temp-1", "dst")
        self.assertEqual(gs.jobs["test-rename-job"]["recording_name"], "dst")

    def test_failed_rename_does_not_retarget(self):
        # dst already exists on disk -> rename_session raises FileExistsError,
        # which must propagate BEFORE any job is retargeted.
        self._make_job("test-rename-job", "temp-1")
        with patch.object(gs, "rename_session", side_effect=FileExistsError("dst")):
            with self.assertRaises(FileExistsError):
                gs.rename_and_retarget_session("temp-1", "dst")
        self.assertEqual(gs.jobs["test-rename-job"]["recording_name"], "temp-1")

    def test_missing_src_still_retargets(self):
        # A temp session with no file yet (no image/save landed) — nothing to
        # collide with, so the live job is still retargeted; the caller treats
        # the FileNotFoundError as a harmless "nothing to move" signal.
        self._make_job("test-rename-job", "temp-1")
        with patch.object(gs, "rename_session", side_effect=FileNotFoundError("temp-1")):
            with self.assertRaises(FileNotFoundError):
                gs.rename_and_retarget_session("temp-1", "dst")
        self.assertEqual(gs.jobs["test-rename-job"]["recording_name"], "dst")

    def test_terminal_job_not_retargeted(self):
        self._make_job("test-rename-job", "temp-1", status="done")
        with patch.object(gs, "rename_session", return_value="dst"):
            gs.rename_and_retarget_session("temp-1", "dst")
        self.assertEqual(gs.jobs["test-rename-job"]["recording_name"], "temp-1")

    def test_unrelated_job_untouched(self):
        self._make_job("test-rename-job", "other-session")
        with patch.object(gs, "rename_session", return_value="dst"):
            gs.rename_and_retarget_session("temp-1", "dst")
        self.assertEqual(gs.jobs["test-rename-job"]["recording_name"], "other-session")


class ReferenceImageMappingTests(unittest.TestCase):
    """The <REFERENCE_IMAGE_1> placeholder (LTX face-ID image2video) is the mandatory
    primary reference: filled from the pinned image slot 1 when supplied, else falls
    back to the uploaded <INPUT_IMAGE> filename — with no second upload for the
    fallback."""

    TEMPLATE = json.dumps({
        "1": {"inputs": {"image": "<INPUT_IMAGE>"}, "class_type": "LoadImage"},
        "2": {"inputs": {"image": "<REFERENCE_IMAGE_1>"}, "class_type": "LoadImage"},
    })

    def _make_job(self):
        gs.jobs["test-ref-job"] = {
            "status": "pending", "channel": gs._JobChannel(), "images": [],
            "assets": [], "cancel": threading.Event(), "server": "http://s",
            "prompt_id": None, "kind": "video", "workflow_name": "wf",
            "prompt": "p", "summary": "s", "started_at": 0.0,
            "finished_at": None, "error": None,
        }
        return "test-ref-job"

    def tearDown(self):
        gs.jobs.pop("test-ref-job", None)

    def _run_and_capture(self, tmpdir, ref_image_1):
        """Run the core just far enough to capture the workflow handed to
        submit_workflow, then short-circuit via JobCancelled."""
        from ComfyServer import JobCancelled
        (Path(tmpdir) / "wf.json").write_text(self.TEMPLATE)

        captured = {}
        server = MagicMock()
        server.upload_image.side_effect = lambda p: f"up_{Path(p).name}"
        # References upload via upload_media (images/video/audio-agnostic).
        server.upload_media.side_effect = lambda p: f"up_{Path(p).name}"

        def _submit(workflow):
            captured["workflow"] = workflow
            raise JobCancelled()
        server.submit_workflow.side_effect = _submit

        job_id = self._make_job()
        job = gs.jobs[job_id]
        with patch.object(gs, "ComfyServer", return_value=server):
            with self.assertRaises(JobCancelled):
                gs._run_generation_core(
                    job_id, job["channel"], job["cancel"], "p", [],
                    "http://s", "linux", "wf", workflow_dir=Path(tmpdir),
                    input_image=Path("/src/first.png"),
                    input_reference_images=[ref_image_1],
                    duration=2, frames=48, fps=24,
                    video_width=1280, video_height=720,
                )
        return captured["workflow"], server

    def test_pinned_reference_uploaded_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            wf, server = self._run_and_capture(tmp, Path("/src/face.png"))
            self.assertEqual(wf["1"]["inputs"]["image"], "up_first.png")
            self.assertEqual(wf["2"]["inputs"]["image"], "up_face.png")
            # The source image uploads via upload_image; the reference via upload_media.
            self.assertEqual({c.args[0].name for c in server.upload_image.call_args_list}, {"first.png"})
            self.assertEqual({c.args[0].name for c in server.upload_media.call_args_list}, {"face.png"})

    def test_reference_falls_back_to_input_image_without_reupload(self):
        with tempfile.TemporaryDirectory() as tmp:
            wf, server = self._run_and_capture(tmp, None)
            # Reference reuses the first frame's uploaded filename...
            self.assertEqual(wf["1"]["inputs"]["image"], "up_first.png")
            self.assertEqual(wf["2"]["inputs"]["image"], "up_first.png")
            # ...and the source image is uploaded exactly once (no second upload, and
            # the fallback path never touches upload_media).
            self.assertEqual(server.upload_image.call_count, 1)
            server.upload_media.assert_not_called()

    def test_reference_without_any_input_image_fails_clearly(self):
        """A text2video run has no first frame to fall back on. Rather than
        substituting an empty LoadImage name (which fails opaquely inside
        ComfyUI), the job must fail up front naming the missing reference."""
        from ComfyServer import JobCancelled

        template = json.dumps({
            "2": {"inputs": {"image": "<REFERENCE_IMAGE_1>"}, "class_type": "LoadImage"},
        })
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "wf.json").write_text(template)
            server = MagicMock()
            server.submit_workflow.side_effect = JobCancelled

            job_id = self._make_job()
            job = gs.jobs[job_id]
            with patch.object(gs, "ComfyServer", return_value=server):
                with self.assertRaises(ValueError) as ctx:
                    gs._run_generation_core(
                        job_id, job["channel"], job["cancel"], "p", [],
                        "http://s", "linux", "wf", workflow_dir=Path(tmp),
                        input_image=None, input_reference_images=[None],
                        duration=2, frames=48, fps=24,
                        video_width=1280, video_height=720,
                    )
            self.assertIn("REFERENCE_IMAGE_1", str(ctx.exception))
            server.submit_workflow.assert_not_called()

    def test_minimax_slots_fill_supplied_and_strip_missing(self):
        """A MiniMax-style template spanning every indexed slot type, in the TWO-NODE
        convention: image 1 + image 2 + a reference video using both its tracks
        (a second loader pointed at the same clip carries the audio), image 3 and the
        standalone audio absent. Supplied slots upload via upload_media — the shared
        clip only once — and the absent slots' loaders are stripped."""
        from ComfyServer import JobCancelled

        template = json.dumps({
            "img1": {"inputs": {"image": "<REFERENCE_IMAGE_1>"}, "class_type": "LoadImage"},
            "img2": {"inputs": {"image": "<REFERENCE_IMAGE_2>"}, "class_type": "LoadImage"},
            "img3": {"inputs": {"image": "<REFERENCE_IMAGE_3>"}, "class_type": "LoadImage"},
            "vid":  {"inputs": {"video": "<REFERENCE_VIDEO_1>"}, "class_type": "LoadVideo"},
            "vaud": {"inputs": {"audio": "<REFERENCE_VIDEO_AUDIO_1>"}, "class_type": "LoadAudio"},
            "aud":  {"inputs": {"audio": "<REFERENCE_AUDIO_1>"}, "class_type": "LoadAudio"},
            "mm":   {"inputs": {
                "image_1": ["img1", 0], "image_2": ["img2", 0], "image_3": ["img3", 0],
                "ref_video": ["vid", 0], "ref_video_audio": ["vaud", 0],
                "ref_audio": ["aud", 0], "prompt": "<PROMPT>",
            }, "class_type": "MiniMaxH3"},
        })
        captured = {}
        server = MagicMock()
        server.upload_media.side_effect = lambda p: f"up_{Path(p).name}"

        def _submit(workflow):
            captured["workflow"] = workflow
            raise JobCancelled()
        server.submit_workflow.side_effect = _submit

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "wf.json").write_text(template)
            job_id = self._make_job()
            job = gs.jobs[job_id]
            with patch.object(gs, "ComfyServer", return_value=server):
                with self.assertRaises(JobCancelled):
                    gs._run_generation_core(
                        job_id, job["channel"], job["cancel"], "p", [],
                        "http://s", "linux", "wf", workflow_dir=Path(tmp),
                        input_reference_images=[Path("/src/a.png"), Path("/src/b.png"), None],
                        input_reference_videos=[Path("/src/c.mp4"), None, None],
                        input_reference_video_audios=[Path("/src/c.mp4"), None, None],
                        duration=2, frames=48, fps=24,
                        video_width=1280, video_height=720,
                    )
        wf = captured["workflow"]
        # Supplied images 1 & 2 wired; both video loaders hold the same clip; the
        # absent image 3 / standalone audio loaders stripped.
        self.assertEqual(wf["img1"]["inputs"]["image"], "up_a.png")
        self.assertEqual(wf["img2"]["inputs"]["image"], "up_b.png")
        self.assertEqual(wf["vid"]["inputs"]["video"], "up_c.mp4")
        self.assertEqual(wf["vaud"]["inputs"]["audio"], "up_c.mp4")
        for gone in ("img3", "aud"):
            self.assertNotIn(gone, wf)
        # One clip = one upload, even though two tokens use it (ref_upload_cache).
        uploaded = [call.args[0] for call in server.upload_media.call_args_list]
        self.assertEqual(uploaded.count(Path("/src/c.mp4")), 1)
        # The consumer keeps its filled inputs and drops the unconnected optionals.
        mm_inputs = wf["mm"]["inputs"]
        self.assertEqual(mm_inputs["image_1"], ["img1", 0])
        self.assertEqual(mm_inputs["image_2"], ["img2", 0])
        self.assertEqual(mm_inputs["ref_video"], ["vid", 0])
        self.assertEqual(mm_inputs["ref_video_audio"], ["vaud", 0])
        for gone_key in ("image_3", "ref_audio"):
            self.assertNotIn(gone_key, mm_inputs)

    # ---- Optimisation bypasses ----------------------------------------------
    # The consolidated H3 templates chain every optimisation between the UNETLoader
    # and the guider; /video-settings switches them off per run.

    OPT_TEMPLATE = json.dumps({
        "unet":  {"inputs": {"unet_name": "h3.safetensors"}, "class_type": "UNETLoader"},
        "turbo": {"inputs": {"lora_name": "turbo.safetensors", "model": ["unet", 0]},
                  "class_type": "LoraLoaderModelOnly",
                  "_meta": {"title": "[opt:turbo] Load LoRA"}},
        "cache": {"inputs": {"model": ["turbo", 0]},
                  "class_type": "ApplyMiniMaxH3FirstBlockCache",
                  "_meta": {"title": "[opt:cache] MiniMax H3 FirstBlockCache"}},
        "guide": {"inputs": {"model": ["cache", 0], "prompt": "<PROMPT>"},
                  "class_type": "BasicGuider"},
    })

    def _run_opts(self, disabled):
        """Run the core over OPT_TEMPLATE with ``disabled`` bypassed; return the graph."""
        from ComfyServer import JobCancelled

        captured = {}
        server = MagicMock()

        def _submit(workflow):
            captured["workflow"] = workflow
            raise JobCancelled()
        server.submit_workflow.side_effect = _submit

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "wf.json").write_text(self.OPT_TEMPLATE)
            job_id = self._make_job()
            job = gs.jobs[job_id]
            with patch.object(gs, "ComfyServer", return_value=server):
                with self.assertRaises(JobCancelled):
                    gs._run_generation_core(
                        job_id, job["channel"], job["cancel"], "p", [],
                        "http://s", "linux", "wf", workflow_dir=Path(tmp),
                        duration=2, frames=48, fps=24,
                        video_width=1280, video_height=720,
                        disabled_optimizations=disabled,
                    )
        return captured["workflow"]

    def test_disabled_optimisation_is_removed_and_rewired(self):
        wf = self._run_opts({"cache"})
        self.assertNotIn("cache", wf)
        self.assertEqual(wf["guide"]["inputs"]["model"], ["turbo", 0])

    def test_all_optimisations_disabled_collapses_to_the_unet(self):
        wf = self._run_opts({"turbo", "cache"})
        self.assertEqual(wf["guide"]["inputs"]["model"], ["unet", 0])
        self.assertEqual(sorted(wf), ["guide", "unet"])

    def test_no_bypasses_leaves_the_chain_whole(self):
        wf = self._run_opts(set())
        self.assertEqual(wf["guide"]["inputs"]["model"], ["cache", 0])
        self.assertEqual(wf["turbo"]["inputs"]["model"], ["unet", 0])

    # ---- Alternate models ----------------------------------------------------
    # A loader may name several interchangeable models as a comma-separated list; the
    # "@model" suffix on the workflow name picks one. The list must never be submitted.

    VARIANT_TEMPLATE = json.dumps({
        "high": {"inputs": {"unet_name": "hi_int8.safetensors, hi_fp16.safetensors"},
                 "class_type": "UNETLoader"},
        "low":  {"inputs": {"unet_name": "lo_int8.safetensors, lo_fp16.safetensors"},
                 "class_type": "UNETLoader"},
        "guide": {"inputs": {"model": ["high", 0], "prompt": "<PROMPT>"},
                  "class_type": "BasicGuider"},
    })

    def _run_variant(self, workflow_name):
        """Run the core over VARIANT_TEMPLATE under ``workflow_name``; return the graph."""
        from ComfyServer import JobCancelled

        captured = {}
        server = MagicMock()

        def _submit(workflow):
            captured["workflow"] = workflow
            raise JobCancelled()
        server.submit_workflow.side_effect = _submit

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "wf.json").write_text(self.VARIANT_TEMPLATE)
            job_id = self._make_job()
            job = gs.jobs[job_id]
            with patch.object(gs, "ComfyServer", return_value=server):
                with self.assertRaises(JobCancelled):
                    gs._run_generation_core(
                        job_id, job["channel"], job["cancel"], "p", [],
                        "http://s", "linux", workflow_name, workflow_dir=Path(tmp),
                    )
        return captured["workflow"]

    def test_unsuffixed_name_submits_the_first_alternate(self):
        wf = self._run_variant("wf")
        self.assertEqual(wf["high"]["inputs"]["unet_name"], "hi_int8.safetensors")
        self.assertEqual(wf["low"]["inputs"]["unet_name"], "lo_int8.safetensors")

    def test_suffix_picks_the_model_index_paired_across_loaders(self):
        wf = self._run_variant("wf@hi_fp16")
        self.assertEqual(wf["high"]["inputs"]["unet_name"], "hi_fp16.safetensors")
        self.assertEqual(wf["low"]["inputs"]["unet_name"], "lo_fp16.safetensors")

    def test_unknown_model_fails_the_job_with_a_readable_error(self):
        with self.assertRaises(ValueError) as cm:
            self._run_variant("wf@nope")
        self.assertIn("hi_fp16", str(cm.exception))

    # ---- Single-node video tracks -------------------------------------------
    # The documented convention: ONE VHS loader holds <REFERENCE_VIDEO_n> and drives
    # both an IMAGE and an AUDIO consumer input. Unticking a track box must disconnect
    # just that output, leaving the node in place to load the clip for the other one.

    SINGLE_NODE_TEMPLATE = json.dumps({
        "vid": {"inputs": {"video": "<REFERENCE_VIDEO_1>"}, "class_type": "VHS_LoadVideo"},
        "mm":  {"inputs": {"ref_video": ["vid", 0], "ref_video_audio": ["vid", 2],
                           "prompt": "<PROMPT>"},
                "class_type": "MiniMaxH3"},
    })

    def _run_single_node(self, *, video, audio, output_types=("IMAGE", "MASK", "AUDIO"),
                         object_info_error=None):
        """Run the core over SINGLE_NODE_TEMPLATE and return (workflow, server)."""
        from ComfyServer import JobCancelled

        captured = {}
        server = MagicMock()
        server.upload_media.side_effect = lambda p: f"up_{Path(p).name}"
        if object_info_error is not None:
            server.get_node_output_types.side_effect = object_info_error
        else:
            server.get_node_output_types.return_value = list(output_types)

        def _submit(workflow):
            captured["workflow"] = workflow
            raise JobCancelled()
        server.submit_workflow.side_effect = _submit

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "wf.json").write_text(self.SINGLE_NODE_TEMPLATE)
            job_id = self._make_job()
            job = gs.jobs[job_id]
            with patch.object(gs, "ComfyServer", return_value=server):
                with self.assertRaises(JobCancelled):
                    gs._run_generation_core(
                        job_id, job["channel"], job["cancel"], "p", [],
                        "http://s", "linux", "wf", workflow_dir=Path(tmp),
                        input_reference_videos=[video, None, None],
                        input_reference_video_audios=[audio, None, None],
                        duration=2, frames=48, fps=24,
                        video_width=1280, video_height=720,
                    )
        return captured["workflow"], server

    def test_single_node_video_only_drops_audio_link(self):
        clip = Path("/src/c.mp4")
        wf, _ = self._run_single_node(video=clip, audio=None)
        # The loader survives, holding the real uploaded name (not the locator marker).
        self.assertEqual(wf["vid"]["inputs"]["video"], "up_c.mp4")
        self.assertEqual(wf["mm"]["inputs"]["ref_video"], ["vid", 0])
        self.assertNotIn("ref_video_audio", wf["mm"]["inputs"])

    def test_single_node_audio_only_drops_image_link(self):
        clip = Path("/src/c.mp4")
        wf, _ = self._run_single_node(video=None, audio=clip)
        # The node MUST stay even with no video track: it still loads the clip.
        self.assertEqual(wf["vid"]["inputs"]["video"], "up_c.mp4")
        self.assertEqual(wf["mm"]["inputs"]["ref_video_audio"], ["vid", 2])
        self.assertNotIn("ref_video", wf["mm"]["inputs"])

    def test_single_node_both_tracks_keeps_both_links_without_object_info(self):
        clip = Path("/src/c.mp4")
        wf, server = self._run_single_node(video=clip, audio=clip)
        self.assertEqual(wf["mm"]["inputs"]["ref_video"], ["vid", 0])
        self.assertEqual(wf["mm"]["inputs"]["ref_video_audio"], ["vid", 2])
        # The hot path must not cost an /object_info round trip, nor a second upload.
        server.get_node_output_types.assert_not_called()
        self.assertEqual(server.upload_media.call_count, 1)

    def test_single_node_inactive_slot_strips_node(self):
        wf, server = self._run_single_node(video=None, audio=None)
        self.assertNotIn("vid", wf)
        for key in ("ref_video", "ref_video_audio"):
            self.assertNotIn(key, wf["mm"]["inputs"])
        server.get_node_output_types.assert_not_called()

    def test_single_node_falls_back_to_input_names_when_object_info_fails(self):
        wf, _ = self._run_single_node(
            video=Path("/src/c.mp4"), audio=None,
            object_info_error=requests.exceptions.ConnectionError("down"))
        # Classified by the consumer input name containing "audio".
        self.assertEqual(wf["vid"]["inputs"]["video"], "up_c.mp4")
        self.assertEqual(wf["mm"]["inputs"]["ref_video"], ["vid", 0])
        self.assertNotIn("ref_video_audio", wf["mm"]["inputs"])

    def test_single_node_raises_when_track_cannot_be_identified(self):
        """No declared output types and no distinguishable input names: fail loudly
        rather than submit a graph that does the opposite of what was ticked."""
        from ComfyServer import JobCancelled

        template = json.dumps({
            "vid": {"inputs": {"video": "<REFERENCE_VIDEO_1>"},
                    "class_type": "VHS_LoadVideo"},
            "mm":  {"inputs": {"in_a": ["vid", 0], "in_b": ["vid", 2],
                               "prompt": "<PROMPT>"},
                    "class_type": "MiniMaxH3"},
        })
        server = MagicMock()
        server.upload_media.side_effect = lambda p: f"up_{Path(p).name}"
        server.get_node_output_types.return_value = []
        server.submit_workflow.side_effect = AssertionError("should not submit")

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "wf.json").write_text(template)
            job_id = self._make_job()
            job = gs.jobs[job_id]
            with patch.object(gs, "ComfyServer", return_value=server):
                with self.assertRaises(ValueError) as ctx:
                    gs._run_generation_core(
                        job_id, job["channel"], job["cancel"], "p", [],
                        "http://s", "linux", "wf", workflow_dir=Path(tmp),
                        input_reference_videos=[Path("/src/c.mp4"), None, None],
                        duration=2, frames=48, fps=24,
                        video_width=1280, video_height=720,
                    )
        msg = str(ctx.exception)
        self.assertIn("reference video 1", msg)
        self.assertIn("VHS_LoadVideo", msg)

    def test_optional_image_slot_stripped_when_absent(self):
        """The optional image slots (2–9) have no INPUT_IMAGE fallback: an absent
        slot strips its loader rather than failing, so the graph can run on any
        subset of references. (Slot 1 is the mandatory reference, tested above.)"""
        from ComfyServer import JobCancelled

        template = json.dumps({
            "img1": {"inputs": {"image": "<REFERENCE_IMAGE_1>"}, "class_type": "LoadImage"},
            "img2": {"inputs": {"image": "<REFERENCE_IMAGE_2>"}, "class_type": "LoadImage"},
            "mm":   {"inputs": {"image_1": ["img1", 0], "image_2": ["img2", 0],
                                "prompt": "<PROMPT>"},
                     "class_type": "MiniMaxH3"},
        })
        captured = {}
        server = MagicMock()
        server.upload_media.side_effect = lambda p: f"up_{Path(p).name}"

        def _submit(workflow):
            captured["workflow"] = workflow
            raise JobCancelled()
        server.submit_workflow.side_effect = _submit

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "wf.json").write_text(template)
            job_id = self._make_job()
            job = gs.jobs[job_id]
            with patch.object(gs, "ComfyServer", return_value=server):
                with self.assertRaises(JobCancelled):
                    gs._run_generation_core(
                        job_id, job["channel"], job["cancel"], "p", [],
                        "http://s", "linux", "wf", workflow_dir=Path(tmp),
                        # Slot 1 supplied so the mandatory reference resolves; slot 2 absent.
                        input_image=None,
                        input_reference_images=[Path("/src/a.png"), None],
                        duration=2, frames=48, fps=24,
                        video_width=1280, video_height=720,
                    )
        wf = captured["workflow"]
        self.assertEqual(wf["img1"]["inputs"]["image"], "up_a.png")
        self.assertNotIn("img2", wf)
        self.assertNotIn("image_2", wf["mm"]["inputs"])
        self.assertEqual(wf["mm"]["inputs"]["image_1"], ["img1", 0])


class ProgressTickTests(CoreSeedRecordingTests):
    """The poll loop's 2s heartbeat carries ComfyUI's progress snapshot.

    Reuses the core fixture (FakeServer's poll_status fires callback(".")) and
    re-enables the listener with a stub, so the payload is asserted without a
    socket.
    """

    class StubListener:
        instances = []

        def __init__(self, server, client_id, node_titles, total_nodes,
                     node_weights=None):
            self.server, self.client_id = server, client_id
            self.node_titles, self.total_nodes = node_titles, total_nodes
            self.node_weights = node_weights
            self.bound, self.stopped = None, False
            self.snapshot = {"percent": 42.5, "phase": "Sampling",
                             "step": 8, "steps": 20,
                             "node_index": 2, "node_total": 4}
            ProgressTickTests.StubListener.instances.append(self)

        def start(self):
            pass

        def bind(self, prompt_id):
            self.bound = prompt_id

        def latest(self):
            return self.snapshot

        def stop(self):
            self.stopped = True

    def setUp(self):
        super().setUp()
        self.StubListener.instances = []
        self._ws = [patch.object(gs, "COMFY_WS_PROGRESS", True),
                    patch.object(gs, "ProgressListener", self.StubListener)]
        for p in self._ws:
            p.start()

    def tearDown(self):
        for p in self._ws:
            p.stop()
        super().tearDown()

    def _ticks(self):
        return [m for m in _drain(self.channel) if m["type"] == "tick"]

    def test_tick_carries_the_snapshot(self):
        self._run()
        self.assertEqual(self._ticks(), [{
            "type": "tick", "percent": 42.5, "phase": "Sampling",
            "step": 8, "steps": 20, "node_index": 2, "node_total": 4,
        }])

    def test_listener_is_bound_to_the_prompt_and_stopped(self):
        self._run()
        lis = self.StubListener.instances[0]
        self.assertEqual(lis.bound, "prompt-id-1234")
        self.assertTrue(lis.stopped)
        self.assertEqual(lis.server, "127.0.0.1:8188")
        self.assertEqual(lis.client_id, "client-id-1234")

    def test_listener_sees_the_submitted_graph(self):
        self._run()
        lis = self.StubListener.instances[0]
        self.assertEqual(lis.total_nodes, len(self.submitted["workflow"]))
        self.assertEqual(set(lis.node_titles), set(self.submitted["workflow"]))

    def test_listener_is_weighted_by_the_submitted_graph(self):
        # The weights must describe the graph as *submitted* — after placeholder
        # substitution and the LoRA/optimisation/reference node surgery — since
        # that is what ComfyUI will actually run.
        self._run()
        lis = self.StubListener.instances[0]
        self.assertEqual(set(lis.node_weights), set(self.submitted["workflow"]))

    def test_listener_is_stopped_even_when_the_job_fails(self):
        # Otherwise a cancel, retry or timeout leaks the reader thread.
        with patch.object(gs.ComfyServer, "get_output_images",
                          lambda self, data: []):
            with self.assertRaises(ValueError):
                self._run()
        self.assertTrue(self.StubListener.instances[0].stopped)

    def test_no_snapshot_yields_a_bare_tick(self):
        # A ComfyUI whose feed never connected must look exactly like it did
        # before this existed, so the client keeps its indeterminate marquee.
        with patch.object(self.StubListener, "latest", lambda self: None):
            self._run()
        self.assertEqual(self._ticks(), [{"type": "tick"}])

    def test_disabled_by_config_yields_a_bare_tick(self):
        with patch.object(gs, "COMFY_WS_PROGRESS", False):
            self._run()
        self.assertEqual(self._ticks(), [{"type": "tick"}])
        self.assertEqual(self.StubListener.instances, [])

    def test_a_broken_listener_never_fails_the_job(self):
        def boom(*a, **k):
            raise RuntimeError("no websocket module")
        with patch.object(gs, "ProgressListener", boom):
            urls = self._run()
        self.assertEqual(len(urls), 1)
        self.assertEqual(self._ticks(), [{"type": "tick"}])


if __name__ == "__main__":
    unittest.main()
