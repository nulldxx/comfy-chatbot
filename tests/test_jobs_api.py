"""Tests for the /jobs view: GET /api/jobs, DELETE /api/jobs/<id>, SSE replay,
and the eviction logic in generation_service.

The actual generation thread is never started — we populate gs.jobs directly
with synthetic records to exercise the surrounding plumbing."""
import json
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import generation_service as gs
from app import app


def _auth(client):
    with client.session_transaction() as sess:
        sess["authenticated"] = True


def _make_record(status="running", kind="video", started_at=None,
                 finished_at=None, assets=None, prompt="a cat dancing",
                 workflow_name="wan22_14B_i2v.json", error=None, recording_name=None):
    return {
        "status": status,
        "channel": gs._JobChannel(),
        "images": [],
        "assets": assets or [],
        "cancel": threading.Event(),
        "server": "host:8188",
        "prompt_id": None,
        "kind": kind,
        "workflow_name": workflow_name,
        "prompt": prompt,
        "summary": gs._build_summary(workflow_name, prompt, kind),
        "started_at": started_at if started_at is not None else time.time(),
        "finished_at": finished_at,
        "error": error,
        "recording_name": recording_name,
    }


class _JobsFixture(unittest.TestCase):
    def setUp(self):
        app.testing = True
        self.client = app.test_client()
        _auth(self.client)
        # Snapshot and clear the global jobs dict so tests don't see each other.
        self._saved_jobs = dict(gs.jobs)
        gs.jobs.clear()

    def tearDown(self):
        gs.jobs.clear()
        gs.jobs.update(self._saved_jobs)


class ApiJobsTests(_JobsFixture):
    def test_lists_image_video_and_sequence_run_jobs(self):
        # "sequence" (Grok expansion only, not long-running) stays excluded, but
        # "sequence-run" (the server-driven expand-and-generate loop) is included
        # so it's visible/cancellable in /jobs and findable by reattachLiveSequenceRun.
        gs.jobs["a"] = _make_record(kind="image")
        gs.jobs["b"] = _make_record(kind="video")
        gs.jobs["c"] = _make_record(kind="sequence")
        gs.jobs["d"] = _make_record(kind="sequence-run", recording_name="temp-1")
        resp = self.client.get("/api/jobs")
        self.assertEqual(resp.status_code, 200)
        ids = {item["job_id"] for item in resp.get_json()}
        self.assertEqual(ids, {"a", "b", "d"})

    def test_sequence_run_exposes_recording_name(self):
        gs.jobs["a"] = _make_record(kind="sequence-run", recording_name="my-run")
        item = self.client.get("/api/jobs").get_json()[0]
        self.assertEqual(item["recording_name"], "my-run")

    def test_image_job_recording_name_is_none(self):
        gs.jobs["a"] = _make_record(kind="image")
        item = self.client.get("/api/jobs").get_json()[0]
        self.assertIsNone(item["recording_name"])

    def test_newest_first_and_capped_at_ten(self):
        for i in range(15):
            gs.jobs[f"j{i}"] = _make_record(started_at=1000 + i)
        items = self.client.get("/api/jobs").get_json()
        self.assertEqual(len(items), 10)
        # Newest (j14) first, oldest of returned page is j5
        self.assertEqual(items[0]["job_id"], "j14")
        self.assertEqual(items[-1]["job_id"], "j5")

    def test_done_job_includes_assets_and_summary(self):
        gs.jobs["done1"] = _make_record(
            status="done", kind="video",
            assets=["/images/20260621_x.mp4"],
            finished_at=time.time(),
        )
        item = self.client.get("/api/jobs").get_json()[0]
        self.assertEqual(item["status"], "done")
        self.assertEqual(item["assets"], ["/images/20260621_x.mp4"])
        self.assertIn("wan22_14B_i2v", item["summary"])
        self.assertIn("a cat dancing", item["summary"])

    def test_falls_back_to_images_when_assets_missing(self):
        rec = _make_record(status="done", kind="image")
        rec["assets"] = []
        rec["images"] = ["/images/legacy.png"]
        gs.jobs["leg"] = rec
        item = self.client.get("/api/jobs").get_json()[0]
        self.assertEqual(item["assets"], ["/images/legacy.png"])


class DismissJobTests(_JobsFixture):
    def test_dismiss_terminal_job_removes_it(self):
        gs.jobs["t"] = _make_record(status="done", finished_at=time.time())
        resp = self.client.delete("/api/jobs/t")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("t", gs.jobs)

    def test_dismiss_running_job_rejected(self):
        gs.jobs["r"] = _make_record(status="running")
        resp = self.client.delete("/api/jobs/r")
        self.assertEqual(resp.status_code, 409)
        self.assertIn("r", gs.jobs)

    def test_dismiss_unknown_job_404(self):
        resp = self.client.delete("/api/jobs/nope")
        self.assertEqual(resp.status_code, 404)


class ProgressReplayTests(_JobsFixture):
    def test_finished_job_replays_events_and_closes_stream(self):
        # Populate a finished job whose channel already holds the full history.
        rec = _make_record(status="done", finished_at=time.time(),
                           assets=["/images/x.png"])
        rec["channel"].send(json.dumps({"type": "progress", "message": "Submitting…"}))
        rec["channel"].send(json.dumps(
            {"type": "done", "images": ["/images/x.png"]}
        ))
        rec["channel"].close()
        gs.jobs["finished"] = rec

        resp = self.client.get("/api/progress/finished")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        # Both cached events should have been replayed before the stream ended.
        self.assertIn('"type": "progress"', body)
        self.assertIn('"type": "done"', body)
        self.assertIn('/images/x.png', body)

    def test_unknown_job_returns_404(self):
        resp = self.client.get("/api/progress/no-such-job")
        self.assertEqual(resp.status_code, 404)


class EvictionTests(_JobsFixture):
    def test_old_terminal_jobs_dropped(self):
        old = _make_record(status="done")
        old["finished_at"] = time.time() - gs.TERMINAL_JOB_KEEP_SECONDS - 1
        gs.jobs["old"] = old
        gs.jobs["fresh"] = _make_record(status="done", finished_at=time.time())
        with gs.jobs_lock:
            gs._evict_old_jobs_locked()
        self.assertNotIn("old", gs.jobs)
        self.assertIn("fresh", gs.jobs)

    def test_running_jobs_never_evicted_by_age(self):
        running = _make_record(status="running")
        running["started_at"] = 0  # ancient
        gs.jobs["r"] = running
        with gs.jobs_lock:
            gs._evict_old_jobs_locked()
        self.assertIn("r", gs.jobs)

    def test_caps_terminal_count(self):
        # MAX_TERMINAL_JOBS + 5 terminal records (all recent) — extras trimmed.
        n = gs.MAX_TERMINAL_JOBS + 5
        now = time.time()
        for i in range(n):
            rec = _make_record(status="done")
            rec["finished_at"] = now + i  # all within the keep window
            gs.jobs[f"j{i}"] = rec
        with gs.jobs_lock:
            gs._evict_old_jobs_locked()
        self.assertEqual(len(gs.jobs), gs.MAX_TERMINAL_JOBS)
        # Oldest five (j0..j4) should have been evicted; j5..j(n-1) survive.
        self.assertNotIn("j0", gs.jobs)
        self.assertIn(f"j{n-1}", gs.jobs)


class StartGenerationJobMetadataTests(_JobsFixture):
    def test_classifies_video_when_video_settings_passed(self):
        # Patch run_generation so no thread actually runs.
        from unittest.mock import patch
        with patch.object(gs, "run_generation"):
            jid = gs.start_generation_job(
                "a cat dancing", [], "host:8188", "linux", "wan22_14B_i2v.json",
                duration=5.0, frames=120, fps=24, video_width=1280, video_height=720,
            )
        rec = gs.jobs[jid]
        self.assertEqual(rec["kind"], "video")
        self.assertEqual(rec["workflow_name"], "wan22_14B_i2v.json")
        self.assertIn("a cat dancing", rec["summary"])

    def test_classifies_image_when_no_video_settings(self):
        from unittest.mock import patch
        with patch.object(gs, "run_generation"):
            jid = gs.start_generation_job(
                "portrait of a person", [], "host:8188", "linux", "sdxl.json",
            )
        self.assertEqual(gs.jobs[jid]["kind"], "image")


if __name__ == "__main__":
    unittest.main()
