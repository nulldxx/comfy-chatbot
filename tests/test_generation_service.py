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
        # A mid-run rename (retarget_live_jobs) must redirect later appends. The
        # name is re-read fresh each iteration, so a rename applied after the first
        # append is picked up for the second.
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


if __name__ == "__main__":
    unittest.main()
