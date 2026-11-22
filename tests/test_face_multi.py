"""Tests for multi-face face-detail: the Pillow maths in face_multi.py, the
run_face_detail_multi job, and the /api/face-detail-multi route."""
import base64
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import face_multi
import generation_service as gs
import image_store as image_store_module
import app as app_module
from app import app


def _mask(size, box):
    m = Image.new("L", size, 0)
    if box:
        m.paste(255, box)
    return m


class MaskBboxTests(unittest.TestCase):
    def test_empty_mask_is_none(self):
        self.assertIsNone(face_multi.mask_bbox(_mask((50, 50), None)))

    def test_bbox_of_painted_region(self):
        self.assertEqual(face_multi.mask_bbox(_mask((50, 50), (10, 12, 20, 30))),
                         (10, 12, 20, 30))

    def test_faint_antialiased_pixels_ignored(self):
        m = _mask((50, 50), (10, 10, 20, 20))
        m.putpixel((40, 40), 60)
        self.assertEqual(face_multi.mask_bbox(m), (10, 10, 20, 20))


class SquareBoxTests(unittest.TestCase):
    def test_centred_square_with_margin(self):
        box = face_multi.square_box((40, 40, 60, 50), 20, 200, 200)
        self.assertEqual(box[2] - box[0], 60)
        self.assertEqual(box[3] - box[1], 60)
        self.assertTrue(box[0] <= 20 and box[2] >= 80)
        self.assertTrue(box[1] <= 25 and box[3] >= 70)

    def test_square_covers_bbox_plus_margin(self):
        bbox = (30, 70, 90, 100)
        l, t, r, b = face_multi.square_box(bbox, 10, 300, 300)
        self.assertLessEqual(l, bbox[0] - 10)
        self.assertGreaterEqual(r, bbox[2] + 10)
        self.assertLessEqual(t, bbox[1] - 10)
        self.assertGreaterEqual(b, bbox[3] + 10)

    def test_shifted_inside_at_corner(self):
        box = face_multi.square_box((0, 0, 10, 10), 20, 100, 80)
        self.assertEqual(box, (0, 0, 50, 50))

    def test_shifted_inside_at_far_edge(self):
        box = face_multi.square_box((90, 70, 100, 80), 20, 100, 80)
        self.assertEqual(box, (50, 30, 100, 80))

    def test_face_bigger_than_image_capped(self):
        box = face_multi.square_box((0, 0, 100, 80), 20, 100, 80)
        self.assertEqual(box[2] - box[0], 80)
        self.assertTrue(0 <= box[0] and box[2] <= 100 and box[1] == 0 and box[3] == 80)


class FeatherAlphaTests(unittest.TestCase):
    def test_opaque_centre_ramp_and_transparent_edge(self):
        box = (0, 0, 60, 60)
        alpha = face_multi.feather_alpha(box, (20, 20, 40, 40))
        self.assertEqual(alpha.size, (60, 60))
        self.assertEqual(alpha.getpixel((30, 30)), 255)
        self.assertEqual(alpha.getpixel((20, 20)), 255)
        self.assertEqual(alpha.getpixel((0, 30)), 0)
        mid = alpha.getpixel((10, 30))
        self.assertTrue(80 < mid < 180, mid)
        self.assertLess(alpha.getpixel((5, 30)), mid)

    def test_zero_band_is_hard(self):
        alpha = face_multi.feather_alpha((10, 10, 30, 30), (10, 10, 30, 30))
        self.assertEqual(alpha.getextrema(), (255, 255))

    def test_edge_touching_side_is_opaque(self):
        # Square shifted to the image edge: no band on the left, so the left
        # edge stays opaque while the right still fades.
        alpha = face_multi.feather_alpha((0, 0, 50, 50), (0, 10, 30, 40))
        self.assertEqual(alpha.getpixel((0, 25)), 255)
        self.assertEqual(alpha.getpixel((49, 25)), 0)


class CropPasteTests(unittest.TestCase):
    def test_prepare_crop_upscales_small_crops(self):
        img = Image.new("RGB", (200, 200), (10, 20, 30))
        crop = face_multi.prepare_crop(img, (10, 10, 110, 110), target=256)
        self.assertEqual(crop.size, (256, 256))

    def test_prepare_crop_keeps_large_crops_native(self):
        img = Image.new("RGB", (300, 300))
        crop = face_multi.prepare_crop(img, (0, 0, 300, 300), target=256)
        self.assertEqual(crop.size, (300, 300))

    def test_paste_back_changes_only_pixels_inside_box(self):
        base = Image.new("RGB", (100, 100), (0, 0, 0))
        processed = Image.new("RGB", (256, 256), (255, 255, 255))
        box = (20, 20, 60, 60)
        face_multi.paste_back(base, processed, box,
                              face_multi.feather_alpha(box, (30, 30, 50, 50)))
        self.assertEqual(base.getpixel((40, 40)), (255, 255, 255))
        self.assertEqual(base.getpixel((10, 10)), (0, 0, 0))
        self.assertEqual(base.getpixel((70, 40)), (0, 0, 0))
        self.assertEqual(base.getpixel((20, 40)), (0, 0, 0))


class RunFaceDetailMultiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.images = self.tmp / "images"
        self.images.mkdir()
        self.patchers = [
            patch.object(gs, "IMAGES_DIR", self.images),
            patch.object(gs, "FACE_MULTI_INPUTS_DIR", self.images / ".face-multi-inputs"),
        ]
        for p in self.patchers:
            p.start()
        self.src = self.images / "src.png"
        Image.new("RGB", (200, 100), (0, 0, 0)).save(self.src)
        self.masks = []
        for i, box in enumerate([(20, 20, 40, 40), (140, 50, 170, 80)]):
            path = self.tmp / f"mask{i}.png"
            _mask((200, 100), box).save(path)
            self.masks.append(path)
        self.calls = []

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_core(self, colour):
        def core(job_id, channel, cancel_event, prompt, loras, *a, **kw):
            self.calls.append((prompt, kw))
            self.assertTrue(Path(kw["input_image"]).is_file())
            self.assertTrue(kw["cleanup_input_image"])
            name = f"out_{len(self.calls)}.png"
            Image.new("RGB", (1024, 1024), colour).save(self.images / name)
            return [f"/images/{name}"]
        return core

    def _start(self, faces, core, margin=10):
        with patch.object(gs, "_run_generation_core", side_effect=core), \
             patch.object(gs.threading, "Thread") as thread:
            job_id = gs.start_face_detail_multi_job(
                faces, self.src, margin, "srv:1", "linux", "fd.json", denoise=0.4)
            args = thread.call_args.kwargs["args"]
            kwargs = thread.call_args.kwargs["kwargs"]
            gs.run_face_detail_multi(*args, **kwargs)
        return job_id

    def _faces(self):
        return [{"prompt": "alice", "loras": [], "mask": self.masks[0]},
                {"prompt": "bob", "loras": [], "mask": self.masks[1]}]

    def test_composites_each_face_and_emits_one_image(self):
        job_id = self._start(self._faces(), self._fake_core((255, 255, 255)))
        events = [json.loads(m) for m in gs.jobs[job_id]["channel"].snapshot()]
        done = [e for e in events if e["type"] == "done"]
        self.assertEqual(len(done), 1)
        self.assertEqual(len(done[0]["images"]), 1)
        self.assertEqual([c[0] for c in self.calls], ["alice", "bob"])
        self.assertEqual(self.calls[0][1]["denoise"], 0.4)
        final = self.images / done[0]["images"][0].rsplit("/", 1)[1]
        with Image.open(final) as img:
            self.assertEqual(img.size, (200, 100))
            self.assertEqual(img.getpixel((30, 30)), (255, 255, 255))
            self.assertEqual(img.getpixel((155, 65)), (255, 255, 255))
            self.assertEqual(img.getpixel((100, 50)), (0, 0, 0))
        # Intermediates, crops and masks are gone; the source is untouched.
        self.assertFalse((self.images / "out_1.png").exists())
        self.assertFalse((self.images / "out_2.png").exists())
        self.assertEqual(list((self.images / ".face-multi-inputs").iterdir()), [])
        self.assertFalse(any(m.exists() for m in self.masks))
        self.assertTrue(self.src.exists())
        self.assertEqual(gs.jobs[job_id]["status"], "done")

    def test_empty_masks_are_skipped(self):
        empty = self.tmp / "empty.png"
        _mask((200, 100), None).save(empty)
        faces = self._faces()[:1] + [{"prompt": "nobody", "loras": [], "mask": empty}]
        self._start(faces, self._fake_core((9, 9, 9)))
        self.assertEqual([c[0] for c in self.calls], ["alice"])

    def test_cancel_between_faces(self):
        def core(job_id, channel, cancel_event, *a, **kw):
            cancel_event.set()
            return self._fake_core((1, 1, 1))(job_id, channel, cancel_event, *a, **kw)
        job_id = self._start(self._faces(), core)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(gs.jobs[job_id]["status"], "cancelled")
        self.assertEqual([p.name for p in self.images.glob("*facemulti*")], [])
        self.assertFalse((self.images / "out_1.png").exists())

    def test_error_cleans_up(self):
        def core(*a, **kw):
            raise RuntimeError("comfy down")
        job_id = self._start(self._faces(), core)
        self.assertEqual(gs.jobs[job_id]["status"], "error")
        self.assertFalse(any(m.exists() for m in self.masks))


class FaceDetailMultiRouteTests(unittest.TestCase):
    def setUp(self):
        app.testing = True
        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True
        self.tmp = Path(tempfile.mkdtemp())
        self.images = self.tmp / "images"
        (self.images / ".masks").mkdir(parents=True)
        Image.new("RGB", (64, 64)).save(self.images / "src.png")
        self.patchers = [
            patch.object(app_module, "IMAGES_DIR", self.images),
            patch.object(image_store_module, "IMAGES_DIR", self.images),
            patch.object(image_store_module, "MASKS_DIR", self.images / ".masks"),
            patch.object(app_module, "list_facedetailer_workflows", return_value=["fd"]),
            patch.object(app_module, "output_storage_error", return_value=None),
        ]
        for p in self.patchers:
            p.start()
        self.start = patch.object(app_module, "start_face_detail_multi_job",
                                  return_value="job-1").start()

    def tearDown(self):
        patch.stopall()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _token(self):
        buf = io.BytesIO()
        _mask((64, 64), (10, 10, 20, 20)).save(buf, "PNG")
        resp = self.client.post("/api/upload-mask",
                                json={"data": base64.b64encode(buf.getvalue()).decode()})
        return resp.get_json()["token"]

    def _post(self, **body):
        payload = {"image": "/images/src.png", "workflow": "fd",
                   "faces": [{"mask": self._token(), "prompt": "a <lora:x:0.5>"}]}
        payload.update(body)
        return self.client.post("/api/face-detail-multi", json=payload)

    def test_happy_path_starts_job(self):
        resp = self._post(margin=30, denoise=0.3)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["job_id"], "job-1")
        args, kwargs = self.start.call_args
        faces, image_path, margin = args[0], args[1], args[2]
        self.assertEqual(faces[0]["prompt"], "a")
        self.assertEqual(faces[0]["loras"][0][0], "x")
        self.assertTrue(faces[0]["mask"].is_file())
        self.assertEqual(image_path.name, "src.png")
        self.assertEqual(margin, 30)
        self.assertEqual(kwargs["denoise"], 0.3)

    def test_no_faces(self):
        self.assertEqual(self._post(faces=[]).status_code, 400)

    def test_too_many_faces(self):
        faces = [{"mask": "t", "prompt": "p"}] * 5
        self.assertEqual(self._post(faces=faces).status_code, 400)

    def test_empty_prompt(self):
        resp = self._post(faces=[{"mask": "t", "prompt": "<lora:x:1>"}])
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Face 1", resp.get_json()["error"])

    def test_bad_token(self):
        resp = self._post(faces=[{"mask": "nope", "prompt": "p"}])
        self.assertEqual(resp.status_code, 404)
        self.start.assert_not_called()

    def test_bad_margin(self):
        self.assertEqual(self._post(margin=500).status_code, 400)
        self.assertEqual(self._post(margin="x").status_code, 400)

    def test_rejected_request_keeps_tokens_usable(self):
        token = self._token()
        bad = self.client.post("/api/face-detail-multi", json={
            "image": "/images/src.png", "margin": -1,
            "faces": [{"mask": token, "prompt": "p"}]})
        self.assertEqual(bad.status_code, 400)
        good = self.client.post("/api/face-detail-multi", json={
            "image": "/images/src.png", "workflow": "fd",
            "faces": [{"mask": token, "prompt": "p"}]})
        self.assertEqual(good.status_code, 200)


if __name__ == "__main__":
    unittest.main()
