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


if __name__ == "__main__":
    unittest.main()
