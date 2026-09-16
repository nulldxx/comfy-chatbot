"""Tests for server-side batch runs.

Two halves: the /api/batch-run route (validation and step resolution, with
start_batch_run_job mocked) and generation_service.run_batch_run (the job loop, with
_run_generation_core and session persistence mocked).
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import catalogue
import generation_service as gs
import image_store as image_store_module
from app import app


def _drain(channel):
    return [json.loads(m) for m in channel.snapshot()]


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

class TestBatchRunRoute(unittest.TestCase):
    def setUp(self):
        app.testing = True
        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True
        self.tmp = tempfile.mkdtemp()
        self.images_dir = Path(self.tmp) / "images"
        self.images_dir.mkdir()
        for name in ("a.png", "b.png", "end.png"):
            (self.images_dir / name).write_bytes(b"x")
        for mod in (app_module, image_store_module):
            p = patch.object(mod, "IMAGES_DIR", self.images_dir)
            p.start()
            self.addCleanup(p.stop)
        for attr, name in (("COMFY_FACEDETAILER_DIR", "facedetailer"),
                           ("COMFY_IMAGE2VIDEO_DIR", "image2video"),
                           ("COMFY_TEXT2VIDEO_DIR", "text2video")):
            d = Path(self.tmp) / name
            d.mkdir()
            (d / f"{name}-wf.json").write_text("{}")
            p = patch.object(catalogue, attr, d)
            p.start()
            self.addCleanup(p.stop)
        job = patch.object(app_module, "start_batch_run_job", return_value="batch-job-id")
        self.job = job.start()
        self.addCleanup(job.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, steps, **extra):
        body = {"recordingName": "My Run", "steps": steps, **extra}
        return self.client.post("/api/batch-run", json=body)

    def parsed(self):
        (steps, settings, recording_name), kwargs = self.job.call_args
        return steps, settings, recording_name, kwargs

    VIDEO = {"i2vWorkflow": "image2video-wf", "t2vWorkflow": "text2video-wf",
             "duration": 5, "frames": 125, "fps": 25, "video_width": 960,
             "video_height": 540, "steps": 8, "video_opts": {"sage": False}}

    def test_t2i_steps_return_job_id(self):
        resp = self.post(
            [{"kind": "t2i", "prompt": "a cat", "label": " (1/2)"},
             {"kind": "t2i", "prompt": "a dog", "workflow": "other"}],
            settings={"server": "http://s", "t2i": {"workflow": "wf", "width": 512,
                                                    "extraPrompt": " monet "}},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["job_id"], "batch-job-id")
        steps, settings, recording_name, kwargs = self.parsed()
        self.assertEqual(recording_name, "my-run")
        self.assertEqual([s["workflow"] for s in steps], ["wf", "other"])
        self.assertEqual(steps[0]["label"], " (1/2)")
        self.assertEqual(steps[0]["workflow_dir"], app_module.COMFY_GENERATION_DIR)
        self.assertEqual(settings["t2i"]["width"], 512)
        self.assertEqual(settings["t2i"]["extraPrompt"], "monet")
        self.assertNotIn("video", settings)
        self.assertEqual(settings["record"]["workflow"], "wf")
        json.dumps(settings["record"])  # persisted with the session: must be JSON-safe
        self.assertEqual(kwargs, {"auto": {}, "seed": None})

    def test_requires_recording_name(self):
        resp = self.client.post("/api/batch-run", json={"steps": [{"kind": "t2i", "prompt": "x"}]})
        self.assertEqual(resp.status_code, 400)
        self.job.assert_not_called()

    def test_empty_steps_400(self):
        self.assertEqual(self.post([]).status_code, 400)
        self.assertEqual(self.post("nope").status_code, 400)
        self.job.assert_not_called()

    def test_too_many_steps_400(self):
        with patch.object(app_module, "BATCH_MAX_STEPS", 2):
            resp = self.post([{"kind": "t2i", "prompt": "x"}] * 3)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("limit is 2", resp.get_json()["error"])

    def test_unknown_kind_400(self):
        resp = self.post([{"kind": "t2i", "prompt": "x"}, {"kind": "upscale"}])
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Step 2", resp.get_json()["error"])
        self.job.assert_not_called()

    def test_prompt_empty_after_lora_tags_400(self):
        resp = self.post([{"kind": "t2i", "prompt": "<lora:x:1>"}])
        self.assertEqual(resp.status_code, 400)
        self.job.assert_not_called()

    def test_i2v_step_resolves_images_and_video_settings(self):
        resp = self.post(
            [{"kind": "i2v", "prompt": "she waves", "image": "/images/a.png",
              "last_frame": "/images/end.png", "sourcePrompt": "a woman",
              "videoMeta": {"description": "waves", "soundscape": None}}],
            settings={"video": self.VIDEO},
        )
        self.assertEqual(resp.status_code, 200)
        steps, settings, _, _ = self.parsed()
        step = steps[0]
        self.assertEqual(step["workflow"], "image2video-wf")
        self.assertEqual(step["input_image"], self.images_dir / "a.png")
        self.assertEqual(step["input_last_frame"], self.images_dir / "end.png")
        self.assertEqual(step["source_prompt"], "a woman")
        self.assertEqual(step["video_meta"], {"description": "waves", "soundscape": ""})
        video = settings["video"]
        self.assertEqual((video["frames"], video["fps"], video["steps"]), (125, 25, 8))
        self.assertEqual(video["disabled_optimizations"], {"sage"})
        self.assertEqual(video["references"]["input_reference_images"], [None] * 9)
        self.assertNotIn("t2i", settings)

    def test_t2v_step_uses_text2video_workflow(self):
        resp = self.post([{"kind": "t2v", "prompt": "a storm"}], settings={"video": self.VIDEO})
        self.assertEqual(resp.status_code, 200)
        step = self.parsed()[0][0]
        self.assertEqual(step["workflow"], "text2video-wf")
        self.assertEqual(step["workflow_dir"], app_module.COMFY_TEXT2VIDEO_DIR)

    def test_missing_source_image_rejected(self):
        resp = self.post([{"kind": "i2v", "prompt": "x", "image": "/images/gone.png"}],
                         settings={"video": self.VIDEO})
        self.assertEqual(resp.status_code, 404)
        resp = self.post([{"kind": "face-detail", "prompt": "a face"}])
        self.assertEqual(resp.status_code, 400)
        self.job.assert_not_called()

    def test_bad_video_settings_400(self):
        resp = self.post([{"kind": "t2v", "prompt": "x"}],
                         settings={"video": dict(self.VIDEO, duration=-1)})
        self.assertEqual(resp.status_code, 400)
        self.job.assert_not_called()

    def test_video_settings_ignored_without_a_video_step(self):
        resp = self.post([{"kind": "t2i", "prompt": "x"}],
                         settings={"video": dict(self.VIDEO, duration=-1)})
        self.assertEqual(resp.status_code, 200)

    def test_unknown_workflow_400(self):
        resp = self.post([{"kind": "face-detail", "prompt": "a face", "image": "/images/a.png",
                           "workflow": "nope"}])
        self.assertEqual(resp.status_code, 400)
        self.job.assert_not_called()

    def test_face_detail_step(self):
        resp = self.post(
            [{"kind": "face-detail", "prompt": "a face <lora:her:1>", "image": "/images/b.png",
              "sourcePrompt": "a woman"}],
            settings={"face": {"workflow": "facedetailer-wf", "denoise": 0.4}},
        )
        self.assertEqual(resp.status_code, 200)
        steps, settings, _, _ = self.parsed()
        self.assertEqual(steps[0]["preserve_mtime_from"], "b.png")
        self.assertEqual(steps[0]["workflow"], "facedetailer-wf")
        self.assertEqual(settings["face"], {"denoise": 0.4})

    def test_auto_face_detail_and_seed(self):
        resp = self.post(
            [{"kind": "t2i", "prompt": "a woman <lora:her:1>"}],
            autoFaceDetail={"workflow": "facedetailer-wf", "denoise": 0.3},
            seed="18446744073709551615",
        )
        self.assertEqual(resp.status_code, 200)
        kwargs = self.parsed()[3]
        self.assertEqual(kwargs["auto"]["face"]["denoise"], 0.3)
        self.assertEqual(kwargs["seed"], 2**64 - 1)

    def test_auto_face_detail_ignored_without_t2i_steps(self):
        resp = self.post([{"kind": "t2v", "prompt": "x"}], settings={"video": self.VIDEO},
                         autoFaceDetail={"workflow": "nope"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.parsed()[3]["auto"], {})

    def test_storage_not_ready_503(self):
        with patch.object(app_module, "output_storage_error",
                          side_effect=lambda: (app_module.jsonify({"error": "locked"}), 503)):
            resp = self.post([{"kind": "t2i", "prompt": "x"}])
        self.assertEqual(resp.status_code, 503)
        self.job.assert_not_called()


# ---------------------------------------------------------------------------
# Job loop
# ---------------------------------------------------------------------------

class RunBatchRunTests(unittest.TestCase):
    JOB = "test-batch-job"

    def setUp(self):
        gs.jobs[self.JOB] = {
            "status": "pending", "channel": gs._JobChannel(), "images": [], "assets": [],
            "cancel": threading.Event(), "retry": threading.Event(), "server": "http://s",
            "prompt_id": None, "session": None, "recording_name": "batch-sess",
            "kind": "batch-run", "workflow_name": "wf", "prompt": "x", "summary": "t",
            "started_at": 0.0, "finished_at": None, "error": None,
        }
        self.tmp = tempfile.mkdtemp()
        self.images = Path(self.tmp)
        (self.images / "src.png").write_bytes(b"x")
        self.calls = []
        self.appended = []

    def tearDown(self):
        gs.jobs.pop(self.JOB, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    REFS = {"input_reference_images": [None] * 9, "input_reference_videos": [None] * 3,
            "input_reference_video_audios": [None] * 3, "input_reference_audios": [None] * 3}

    def settings(self, **overrides):
        s = {"server": "http://s", "server_os": "linux",
             "t2i": {"width": 512, "height": 768, "steps": 20, "extraPrompt": None},
             "video": {"duration": 5.0, "frames": 125, "fps": 25, "video_width": 960,
                       "video_height": 540, "steps": 8, "disabled_optimizations": {"sage"},
                       "references": self.REFS},
             "face": {"denoise": 0.4},
             "record": {"server": "http://s", "workflow": "wf"}}
        s.update(overrides)
        return s

    def core(self, fail=None):
        def fake(jid, channel, cancel, prompt, loras, server, server_os, workflow, **k):
            self.calls.append((workflow, prompt, loras, k))
            if fail:
                fail(workflow, prompt)
            n = len(self.calls)
            ext = "mp4" if workflow in ("i2v-wf", "t2v-wf") else "png"
            return [f"/images/out{n}.{ext}"]
        return fake

    def run_batch(self, steps, settings=None, auto=None, seed=None, fail=None):
        with patch.object(gs, "IMAGES_DIR", self.images), \
             patch.object(gs, "forget_seed"), \
             patch.object(gs, "_run_generation_core", side_effect=self.core(fail)), \
             patch.object(gs, "append_session_image",
                          side_effect=lambda *a, **k: self.appended.append((a, k))), \
             patch.object(gs, "append_failure_to_recording"):
            gs.run_batch_run(self.JOB, steps, settings or self.settings(), auto, seed)
        return _drain(gs.jobs[self.JOB]["channel"])

    @staticmethod
    def t2i(prompt, workflow="wf", label=""):
        return {"kind": "t2i", "prompt": prompt, "workflow": workflow,
                "workflow_dir": Path("/wf/gen"), "label": label}

    def i2v(self, **extra):
        return {"kind": "i2v", "prompt": "she waves", "workflow": "i2v-wf",
                "workflow_dir": Path("/wf/i2v"), "label": "",
                "input_image": self.images / "src.png", "input_last_frame": None,
                "source_prompt": "a woman", "video_meta": {"description": "waves"}, **extra}

    def test_steps_run_in_order_and_record(self):
        msgs = self.run_batch([self.t2i("a cat", label=" (1/2)"), self.t2i("a dog", "wf2")],
                              settings=self.settings(t2i={"width": 512, "height": 768,
                                                          "steps": 20, "extraPrompt": "monet"}))
        self.assertEqual([(c[0], c[1]) for c in self.calls],
                         [("wf", "a cat monet"), ("wf2", "a dog monet")])
        k = self.calls[0][3]
        self.assertEqual((k["width"], k["height"], k["steps"]), (512, 768, 20))
        self.assertTrue(k["track_seed"])
        shots = [m for m in msgs if m["type"] == "shot"]
        self.assertEqual([s["prompt"] for s in shots], ["a cat", "a dog"])
        self.assertEqual(shots[0]["label"], " (1/2)")
        self.assertNotIn("stage", shots[0])
        # Stored prompt is the typed one, without the extraPrompt suffix.
        self.assertEqual([a[1:3] for a, _ in self.appended],
                         [("/images/out1.png", "a cat"), ("/images/out2.png", "a dog")])
        self.assertEqual(self.appended[0][0][0], "batch-sess")
        self.assertEqual(self.appended[0][1]["settings"], {"server": "http://s", "workflow": "wf"})
        done = [m for m in msgs if m["type"] == "done"][0]
        self.assertTrue(done["batch"])
        self.assertEqual(done["images"], ["/images/out1.png", "/images/out2.png"])
        self.assertEqual(gs.jobs[self.JOB]["status"], "done")

    def test_t2i_step_gets_auto_face_detail(self):
        auto = {"face": {"workflow": "face", "workflow_dir": Path("/wf/face"), "denoise": 0.3,
                         "prompt": None, "replacements": []}}
        with patch.object(gs, "_discard_gallery_file") as discard:
            msgs = self.run_batch([self.t2i("a woman smiling <lora:her:0.8>")], auto=auto)
        self.assertEqual([c[0] for c in self.calls], ["wf", "face"])
        self.assertEqual(self.calls[1][1], "a woman's face, smiling")
        discard.assert_called_once_with("/images/out1.png")
        self.assertEqual([m["url"] for m in msgs if m["type"] == "image"], ["/images/out2.png"])

    def test_i2v_step(self):
        msgs = self.run_batch([self.i2v(input_last_frame=self.images / "end.png")])
        workflow, prompt, loras, k = self.calls[0]
        self.assertEqual((workflow, prompt, loras), ("i2v-wf", "she waves", []))
        self.assertEqual(k["input_image"], self.images / "src.png")
        self.assertEqual(k["input_last_frame"], self.images / "end.png")
        self.assertEqual((k["frames"], k["fps"], k["steps"]), (125, 25, 8))
        self.assertEqual(k["disabled_optimizations"], {"sage"})
        # Recorded against the source image's prompt/meta; the user line is the i2v prompt.
        (args, kwargs), = self.appended
        self.assertEqual(args[1:4], ("/images/out1.mp4", "a woman", {"description": "waves"}))
        self.assertEqual(kwargs["message_prompt"], "she waves")
        shot = [m for m in msgs if m["type"] == "shot"][0]
        self.assertEqual(shot["stage"], "image2video")
        image = [m for m in msgs if m["type"] == "image"][0]
        self.assertEqual(image["stage"], "image2video")

    def test_i2v_drops_reference_slot_1_when_it_is_the_source(self):
        src = self.images / "src.png"
        refs = dict(self.REFS, input_reference_images=[src, self.images / "r2.png"] + [None] * 7)
        video = dict(self.settings()["video"], references=refs)
        self.run_batch([self.i2v(), self.i2v(input_image=self.images / "other.png")],
                       settings=self.settings(video=video))
        self.assertEqual(self.calls[0][3]["input_reference_images"][:2],
                         [None, self.images / "r2.png"])
        self.assertEqual(self.calls[1][3]["input_reference_images"][0], src)
        # The shared settings are never mutated by a step.
        self.assertEqual(refs["input_reference_images"][0], src)

    def test_t2v_step(self):
        self.run_batch([{"kind": "t2v", "prompt": "a storm <lora:x:1>", "workflow": "t2v-wf",
                         "workflow_dir": Path("/wf/t2v"), "label": ""}])
        workflow, prompt, loras, k = self.calls[0]
        self.assertEqual((workflow, prompt, loras), ("t2v-wf", "a storm", []))
        self.assertNotIn("input_image", k)
        (args, _), = self.appended
        self.assertEqual(args[1:3], ("/images/out1.mp4", "a storm <lora:x:1>"))

    def test_face_detail_step_keeps_source(self):
        step = {"kind": "face-detail", "prompt": "a face <lora:her:1>", "workflow": "face",
                "workflow_dir": Path("/wf/face"), "label": "",
                "input_image": self.images / "src.png", "preserve_mtime_from": "src.png",
                "source_prompt": "a woman", "video_meta": None}
        msgs = self.run_batch([step])
        workflow, prompt, loras, k = self.calls[0]
        self.assertEqual(prompt, "a face")
        self.assertEqual(len(loras), 1)
        self.assertEqual((k["denoise"], k["preserve_mtime_from"]), (0.4, "src.png"))
        self.assertTrue((self.images / "src.png").exists())
        (args, kwargs), = self.appended
        self.assertEqual(args[1:3], ("/images/out1.png", "a woman"))
        self.assertEqual(kwargs["message_prompt"], "a face <lora:her:1>")
        self.assertEqual([m.get("stage") for m in msgs if m["type"] == "shot"], ["face-detail"])

    def test_seed_pins_only_the_first_primary_step(self):
        face = {"kind": "face-detail", "prompt": "a face <lora:her:1>", "workflow": "face",
                "workflow_dir": Path("/wf/face"), "label": "",
                "input_image": self.images / "src.png", "preserve_mtime_from": "src.png",
                "source_prompt": "", "video_meta": None}
        self.run_batch([face, self.t2i("a"), self.t2i("b")], seed=42)
        self.assertNotIn("seed", self.calls[0][3])
        self.assertEqual([c[3]["seed"] for c in self.calls[1:]], [42, None])

    def test_failed_step_pauses_then_retry_reruns_only_it(self):
        attempts = {"n": 0}

        def fail(workflow, prompt):
            if prompt == "bad":
                attempts["n"] += 1
                if attempts["n"] == 1:
                    gs.jobs[self.JOB]["retry"].set()
                    raise ValueError("boom")

        msgs = self.run_batch([self.t2i("good"), self.t2i("bad"), self.t2i("after")], fail=fail)
        self.assertEqual([c[1] for c in self.calls], ["good", "bad", "bad", "after"])
        self.assertEqual(len([m for m in msgs if m["type"] == "shot_failed"]), 1)
        self.assertEqual(gs.jobs[self.JOB]["status"], "done")
        self.assertEqual(gs.jobs[self.JOB]["failed"], [])

    def test_cancel_mid_batch_keeps_partial_assets(self):
        def fail(workflow, prompt):
            gs.jobs[self.JOB]["cancel"].set()

        msgs = self.run_batch([self.t2i("a"), self.t2i("b")], fail=fail)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(gs.jobs[self.JOB]["status"], "cancelled")
        self.assertEqual(gs.jobs[self.JOB]["assets"], ["/images/out1.png"])
        self.assertEqual(len(self.appended), 1)
        self.assertTrue(any(m["type"] == "cancelled" for m in msgs))

    def test_start_batch_run_job_record(self):
        with patch.object(gs, "run_batch_run"):
            job_id = gs.start_batch_run_job([self.t2i("a cat"), self.t2i("b")],
                                            self.settings(), "sess", auto={}, seed=None)
        try:
            rec = gs.jobs[job_id]
            self.assertEqual(rec["kind"], "batch-run")
            self.assertEqual(rec["recording_name"], "sess")
            self.assertIsNotNone(rec["retry"])
            self.assertIn("+1 more", rec["summary"])
        finally:
            gs.jobs.pop(job_id, None)


if __name__ == "__main__":
    unittest.main()
