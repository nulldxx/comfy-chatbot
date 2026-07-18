"""Tests for the Grok prompt-sequence job runner in generation_service.

run_sequence() runs the (mocked) Grok call inside a tracked job, applies any
find→replace pairs, and emits channel messages (done/cancelled/error) the SSE
endpoint relays to the client. The HTTP layer (grok._chat) is never touched."""
import os
import sys
import json
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import generation_service as gs
from grok import GrokError


def _drain(channel):
    """Collect all messages from a _JobChannel's event log."""
    return [json.loads(m) for m in channel.snapshot()]


class RunSequenceTests(unittest.TestCase):
    def _make_job(self):
        job_id = "test-job"
        gs.jobs[job_id] = {
            "status": "pending",
            "channel": gs._JobChannel(),
            "images": [],
            "assets": [],
            "cancel": threading.Event(),
            "server": None,
            "prompt_id": None,
            "session": None,  # run_sequence calls .close() in finally — guarded below
            "kind": "sequence",
            "workflow_name": None,
            "prompt": "x",
            "summary": "test",
            "started_at": 0.0,
            "finished_at": None,
            "error": None,
        }
        return job_id

    def tearDown(self):
        gs.jobs.pop("test-job", None)

    def test_plain_sequence_done_with_replacements(self):
        job_id = self._make_job()
        with patch.object(gs, "generate_prompt_sequence", return_value=["a cat", "a cat too"]):
            gs.run_sequence(job_id, "x", 2, [("cat", "dog")], video=False)
        msgs = _drain(gs.jobs[job_id]["channel"])
        done = [m for m in msgs if m["type"] == "done"][0]
        self.assertEqual(done["prompts"], ["a dog", "a dog too"])
        self.assertFalse(done["video"])
        self.assertEqual(gs.jobs[job_id]["status"], "done")

    def test_video_sequence_replacements_apply_to_all_fields(self):
        job_id = self._make_job()
        shots = [{"prompt": "a cat sits", "action": "the cat leaps", "audio": "cat meow"}]
        with patch.object(gs, "generate_video_prompt_sequence", return_value=shots):
            gs.run_sequence(job_id, "x", 1, [("cat", "dog")], video=True)
        done = [m for m in _drain(gs.jobs[job_id]["channel"]) if m["type"] == "done"][0]
        self.assertTrue(done["video"])
        item = done["prompts"][0]
        self.assertEqual(item["prompt"], "a dog sits")
        self.assertEqual(item["action"], "the dog leaps")
        self.assertEqual(item["audio"], "dog meow")

    def test_replacements_are_case_insensitive_and_preserve_case(self):
        job_id = self._make_job()
        prompts = ["a bird flies", "a Bird sings", "a BIRD"]
        with patch.object(gs, "generate_prompt_sequence", return_value=prompts):
            gs.run_sequence(job_id, "x", 3, [("bird", "dog")], video=False)
        done = [m for m in _drain(gs.jobs[job_id]["channel"]) if m["type"] == "done"][0]
        self.assertEqual(done["prompts"], ["a dog flies", "a Dog sings", "a DOG"])

    def test_grok_error_emits_error(self):
        job_id = self._make_job()
        with patch.object(gs, "generate_prompt_sequence", side_effect=GrokError("down")):
            gs.run_sequence(job_id, "x", 1, [], video=False)
        msgs = _drain(gs.jobs[job_id]["channel"])
        err = [m for m in msgs if m["type"] == "error"][0]
        self.assertEqual(err["message"], "down")
        self.assertEqual(gs.jobs[job_id]["status"], "error")

    def test_grok_error_after_cancel_reports_cancelled(self):
        # Closing the session aborts the request as a GrokError; with the cancel
        # event set, that surfaces as a cancellation, not an error.
        job_id = self._make_job()
        gs.jobs[job_id]["cancel"].set()
        with patch.object(gs, "generate_prompt_sequence", side_effect=GrokError("aborted")):
            gs.run_sequence(job_id, "x", 1, [], video=False)
        msgs = _drain(gs.jobs[job_id]["channel"])
        self.assertEqual(msgs[-1]["type"], "cancelled")
        self.assertEqual(gs.jobs[job_id]["status"], "cancelled")

    def test_precancelled_job_does_not_call_grok(self):
        job_id = self._make_job()
        gs.jobs[job_id]["cancel"].set()
        with patch.object(gs, "generate_prompt_sequence") as gen:
            gs.run_sequence(job_id, "x", 1, [], video=False)
        gen.assert_not_called()
        self.assertEqual(gs.jobs[job_id]["status"], "cancelled")


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

    def test_per_shot_error_continues(self):
        job_id = self._make_job()

        def core(jid, channel, cancel, prompt, loras, *a, **k):
            if prompt == "bad":
                raise ValueError("boom")
            return ["/images/ok.png"]

        with patch.object(gs, "generate_prompt_sequence", return_value=["bad", "good"]), \
             patch.object(gs, "_run_generation_core", side_effect=core), \
             patch.object(gs, "append_session_image"):
            gs.run_sequence_run(job_id, "x", 2, [], video=False, gen_settings=self._settings())
        images = [m for m in _drain(gs.jobs[job_id]["channel"]) if m["type"] == "image"]
        self.assertEqual(len(images), 1)
        self.assertEqual(gs.jobs[job_id]["status"], "done")

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
        # A failed shot is (1) recorded in the terminal "failed" list, (2) sent as
        # a distinct "failed" SSE event (not just a transient progress line), and
        # (3) persisted to the session via append_failure_to_recording so it's
        # visible after a later /session-load.
        job_id = self._make_job()
        notes = []

        def core(jid, channel, cancel, prompt, loras, *a, **k):
            if prompt == "bad":
                raise ValueError("boom")
            return ["/images/ok.png"]

        with patch.object(gs, "generate_prompt_sequence", return_value=["bad", "good"]), \
             patch.object(gs, "_run_generation_core", side_effect=core), \
             patch.object(gs, "append_session_image"), \
             patch.object(gs, "append_session_note", side_effect=lambda *a, **k: notes.append(a)):
            gs.run_sequence_run(job_id, "x", 2, [], video=False, gen_settings=self._settings())

        msgs = _drain(gs.jobs[job_id]["channel"])
        failed_events = [m for m in msgs if m["type"] == "failed"]
        self.assertEqual(len(failed_events), 1)
        self.assertEqual(failed_events[0]["prompt"], "bad")
        self.assertEqual(failed_events[0]["error"], "boom")

        done = [m for m in msgs if m["type"] == "done"][0]
        self.assertEqual(len(done["failed"]), 1)
        self.assertEqual(done["failed"][0]["prompt"], "bad")
        self.assertEqual(gs.jobs[job_id]["failed"][0]["prompt"], "bad")

        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0][0], "run-sess")  # recording name
        self.assertEqual(notes[0][1], "bad")       # prompt


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


if __name__ == "__main__":
    unittest.main()
