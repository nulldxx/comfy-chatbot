"""Tests for /character-sheet: the Pillow layout in character_sheet.py, the
<WIDTH>/<HEIGHT> output-size tokens in _run_generation_core, the
run_character_sheet job and the /api/character-sheet route."""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import character_sheet as cs
import generation_service as gs
import image_store as image_store_module
import persistence
import seed_store
import workflow
import app as app_module
from app import app


class LayoutTests(unittest.TestCase):
    def test_boxes_inside_canvas_and_disjoint(self):
        W, H = cs.SHEET_SIZE
        boxes = [p["box"] for p in cs.PANELS.values()]
        for x, y, w, h in boxes:
            self.assertTrue(x >= 0 and y >= 0 and x + w <= W and y + h <= H, (x, y, w, h))
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                overlap = (a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
                           and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])
                self.assertFalse(overlap, (a, b))

    def test_generation_sizes_are_multiples_of_16(self):
        for spec in cs.PANELS.values():
            self.assertEqual([v % 16 for v in spec["size"]], [0, 0])

    def test_sheet_is_16_by_9_4k(self):
        self.assertEqual(cs.SHEET_SIZE, (3840, 2160))


class TrimFitTests(unittest.TestCase):
    def test_trim_white_crops_to_figure_plus_pad(self):
        img = Image.new("RGB", (200, 400), (255, 255, 255))
        img.paste((10, 20, 30), (80, 100, 120, 300))    # 40×200 figure
        out = cs.trim_white(img)
        pad = round(200 * cs.TRIM_PAD)
        self.assertEqual(out.size, (40 + 2 * pad, 200 + 2 * pad))

    def test_trim_white_ignores_near_white_noise(self):
        img = Image.new("RGB", (100, 100), (255, 255, 255))
        img.putpixel((5, 5), (250, 250, 250))
        img.paste((0, 0, 0), (40, 40, 60, 60))
        self.assertLess(cs.trim_white(img, pad=0).size[0], 30)

    def test_trim_white_of_blank_image_is_unchanged(self):
        img = Image.new("RGB", (50, 70), (255, 255, 255))
        self.assertEqual(cs.trim_white(img).size, (50, 70))

    def test_fit_into_keeps_aspect_and_centres(self):
        canvas = Image.new("RGB", (300, 300), (255, 255, 255))
        cs.fit_into(canvas, Image.new("RGB", (100, 50), (0, 0, 0)), (0, 0, 300, 300))
        # 100×50 scaled to 300×150, centred vertically at y 75..225.
        self.assertEqual(canvas.getpixel((150, 74)), (255, 255, 255))
        self.assertEqual(canvas.getpixel((150, 76)), (0, 0, 0))
        self.assertEqual(canvas.getpixel((150, 224)), (0, 0, 0))
        self.assertEqual(canvas.getpixel((150, 226)), (255, 255, 255))


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_compose_places_each_panel_in_its_box(self):
        paths = {}
        for i, key in enumerate(cs.PANELS):
            p = self.tmp / f"{key}.png"
            w, h = cs.PANELS[key]["size"]
            Image.new("RGB", (w, h), (i * 30, 0, 0)).save(p)
            paths[key] = p
        sheet = cs.compose_sheet(paths)
        self.assertEqual(sheet.size, cs.SHEET_SIZE)
        self.assertEqual(sheet.getpixel((10, 10)), (255, 255, 255))     # margin
        for i, (key, spec) in enumerate(cs.PANELS.items()):
            x, y, w, h = spec["box"]
            self.assertEqual(sheet.getpixel((x + w // 2, y + h // 2)), (i * 30, 0, 0), key)

    def test_missing_panels_leave_blank_boxes(self):
        sheet = cs.compose_sheet({})
        self.assertEqual(sheet.getcolors(), [(3840 * 2160, (255, 255, 255))])


class DefaultOutputSizeTests(unittest.TestCase):
    def test_square_is_one_megapixel(self):
        self.assertEqual(cs.default_output_size(512, 512), (992, 992))

    def test_aspect_kept_and_snapped(self):
        w, h = cs.default_output_size(3000, 2000)
        self.assertEqual((w % 16, h % 16), (0, 0))
        self.assertAlmostEqual(w / h, 1.5, delta=0.03)
        self.assertAlmostEqual(w * h / 1e6, 1.0, delta=0.05)

    def test_validation_filler_parses_size_tokens(self):
        text = '{"1": {"inputs": {"width": <WIDTH>, "height": <HEIGHT>, "v": <VIDEO_WIDTH>}}}'
        parsed = json.loads(workflow.fill_placeholders_for_validation(text))
        self.assertEqual(parsed["1"]["inputs"]["width"], 1024)


class CoreSizeTokenTests(unittest.TestCase):
    """<WIDTH>/<HEIGHT> in _run_generation_core, against a stub ComfyUI server."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.images_dir = self.tmp / "images"
        self.images_dir.mkdir()
        self.wf_dir = self.tmp / "workflows"
        self.wf_dir.mkdir()
        self.src = self.tmp / "src.png"
        Image.new("RGB", (600, 400)).save(self.src)
        self.submitted = {}
        outer = self

        class FakeServer:
            def __init__(self, address):
                self.client_id = "c"

            def upload_image(self, path):
                return "uploaded.png"

            def submit_workflow(self, wf):
                outer.submitted["workflow"] = wf
                return "pid"

            def poll_status(self, *a, **k):
                return {"outputs": {}}

            def get_output_images(self, prompt_data):
                return ["out.png"]

            def download_images(self, images, dest_dir):
                p = Path(dest_dir) / "out.png"
                p.write_bytes(b"x")
                return [p]

        self.patchers = [
            patch.object(gs, "ComfyServer", FakeServer),
            patch.object(gs, "COMFY_WS_PROGRESS", False),
            patch.object(gs, "IMAGES_DIR", self.images_dir),
            patch.object(gs, "purge_generation_started", lambda *a, **k: None),
            patch.object(gs, "purge_generation_finished", lambda *a, **k: None),
            patch.object(seed_store, "IMAGES_DIR", self.images_dir),
            patch.object(seed_store, "SEEDS_FILE", self.images_dir / ".seeds.json"),
        ]
        for p in self.patchers:
            p.start()
        gs.jobs["job-sz"] = {"prompt_id": None}

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        gs.jobs.pop("job-sz", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, template, **kw):
        (self.wf_dir / "i2i.json").write_text(template)
        gs._run_generation_core("job-sz", gs._JobChannel(), threading.Event(), "p", [],
                                "srv", "linux", "i2i", workflow_dir=self.wf_dir,
                                input_image=self.src, **kw)
        return self.submitted["workflow"]

    SIZED = ('{"1": {"class_type": "EmptyFlux2LatentImage", "inputs": '
             '{"width": <WIDTH>, "height": <HEIGHT>, "image": "<INPUT_IMAGE>", "t": "<PROMPT>"}}}')

    def test_explicit_size_fills_tokens(self):
        wf = self._run(self.SIZED, out_width=768, out_height=1920)
        self.assertEqual((wf["1"]["inputs"]["width"], wf["1"]["inputs"]["height"]), (768, 1920))

    def test_fallback_is_source_at_one_megapixel(self):
        wf = self._run(self.SIZED)
        self.assertEqual((wf["1"]["inputs"]["width"], wf["1"]["inputs"]["height"]),
                         cs.default_output_size(600, 400))

    def test_template_without_tokens_ignores_size(self):
        tpl = '{"1": {"class_type": "X", "inputs": {"image": "<INPUT_IMAGE>", "t": "<PROMPT>"}}}'
        wf = self._run(tpl, out_width=768, out_height=1920)
        self.assertNotIn("width", wf["1"]["inputs"])

    def test_video_width_token_is_not_touched(self):
        tpl = ('{"1": {"class_type": "X", "inputs": {"w": <WIDTH>, "h": <HEIGHT>, '
               '"vw": <VIDEO_WIDTH>, "image": "<INPUT_IMAGE>", "t": "<PROMPT>"}}}')
        (self.wf_dir / "i2i.json").write_text(tpl)
        with self.assertRaises(ValueError) as ctx:
            gs._run_generation_core("job-sz", gs._JobChannel(), threading.Event(), "p", [],
                                    "srv", "linux", "i2i", workflow_dir=self.wf_dir,
                                    input_image=self.src, out_width=64, out_height=64)
        self.assertIn("<VIDEO_WIDTH>", str(ctx.exception))


class RunCharacterSheetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.images = self.tmp / "images"
        self.images.mkdir()
        self.patcher = patch.object(gs, "IMAGES_DIR", self.images)
        self.patcher.start()
        self.src = self.images / "src.png"
        Image.new("RGB", (64, 64), (0, 0, 0)).save(self.src)
        self.calls = []

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _core(self, job_id, channel, cancel_event, prompt, loras, *a, **kw):
        self.calls.append((prompt, kw))
        name = f"panel_{len(self.calls)}.png"
        Image.new("RGB", (kw["out_width"], kw["out_height"]), (200, 0, 0)).save(self.images / name)
        return [f"/images/{name}"]

    def _start(self, panels, compose=True, core=None):
        with patch.object(gs, "_run_generation_core", side_effect=core or self._core), \
             patch.object(gs.threading, "Thread") as thread:
            job_id = gs.start_character_sheet_job(
                self.src, panels, compose, "srv:1", "linux", "klein.json",
                workflow_dir=Path("/x"), denoise=1.0)
            gs.run_character_sheet(*thread.call_args.kwargs["args"],
                                   **thread.call_args.kwargs["kwargs"])
        events = [json.loads(m) for m in gs.jobs[job_id]["channel"].snapshot()]
        return job_id, events

    def _panel(self, key, cached=None):
        return {"key": key, "prompt": f"{key} prompt", "loras": [], "cached": cached}

    def test_generates_uncached_panels_at_their_sizes_and_composites(self):
        cached = self.images / "old_portrait.png"
        Image.new("RGB", (1536, 1536), (0, 0, 200)).save(cached)
        panels = [self._panel("portrait", cached), self._panel("front"), self._panel("smile")]
        job_id, events = self._start(panels)
        self.assertEqual([c[0] for c in self.calls], ["front prompt", "smile prompt"])
        self.assertEqual((self.calls[0][1]["out_width"], self.calls[0][1]["out_height"]), (768, 1920))
        self.assertEqual(self.calls[0][1]["input_image"], self.src)
        self.assertEqual(self.calls[0][1]["denoise"], 1.0)
        self.assertEqual([e["key"] for e in events if e["type"] == "panel"], ["front", "smile"])
        done = [e for e in events if e["type"] == "done"][0]
        self.assertEqual(done["panels"]["portrait"], "/images/old_portrait.png")
        sheet_path = self.images / done["images"][0].rsplit("/", 1)[1]
        with Image.open(sheet_path) as sheet:
            self.assertEqual(sheet.size, cs.SHEET_SIZE)
            x, y, w, h = cs.PANELS["portrait"]["box"]
            self.assertEqual(sheet.getpixel((x + w // 2, y + h // 2)), (0, 0, 200))
        # Panels are the cache, so they are kept.
        self.assertTrue((self.images / "panel_1.png").exists())
        self.assertEqual(gs.jobs[job_id]["status"], "done")
        self.assertIn("/images/panel_1.png", gs.jobs[job_id]["assets"])

    def test_no_compose_emits_no_sheet(self):
        _, events = self._start([self._panel("rear")], compose=False)
        done = [e for e in events if e["type"] == "done"][0]
        self.assertEqual(done["images"], [])
        self.assertEqual(list(self.images.glob("*charsheet*")), [])

    def test_cancel_between_panels(self):
        def core(job_id, channel, cancel_event, *a, **kw):
            cancel_event.set()
            return self._core(job_id, channel, cancel_event, *a, **kw)
        job_id, events = self._start([self._panel("front"), self._panel("side")], core=core)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(gs.jobs[job_id]["status"], "cancelled")
        self.assertEqual(list(self.images.glob("*charsheet*")), [])

    def test_error_reports_panels_done_so_far(self):
        def core(job_id, channel, cancel_event, prompt, *a, **kw):
            if prompt.startswith("side"):
                raise RuntimeError("comfy down")
            return self._core(job_id, channel, cancel_event, prompt, *a, **kw)
        job_id, events = self._start([self._panel("front"), self._panel("side")], core=core)
        err = [e for e in events if e["type"] == "error"][0]
        self.assertIn("front", err["panels"])
        self.assertEqual(gs.jobs[job_id]["status"], "error")


class CharacterSheetRouteTests(unittest.TestCase):
    def setUp(self):
        app.testing = True
        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True
        self.tmp = Path(tempfile.mkdtemp())
        self.images = self.tmp / "images"
        self.images.mkdir()
        Image.new("RGB", (64, 64)).save(self.images / "src.png")
        Image.new("RGB", (64, 64)).save(self.images / "front.png")
        for p in [
            patch.object(app_module, "IMAGES_DIR", self.images),
            patch.object(image_store_module, "IMAGES_DIR", self.images),
            patch.object(app_module, "list_image2image_workflows", return_value=["klein"]),
            patch.object(app_module, "output_storage_error", return_value=None),
        ]:
            p.start()
        self.start = patch.object(app_module, "start_character_sheet_job",
                                  return_value="job-1").start()

    def tearDown(self):
        patch.stopall()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _post(self, **body):
        payload = {"image": "/images/src.png", "workflow": "klein", "compose": True,
                   "panels": [{"key": "portrait", "prompt": "p <lora:x:0.5>"},
                              {"key": "front", "prompt": "f", "cached": "/images/front.png"}]}
        payload.update(body)
        return self.client.post("/api/character-sheet", json=payload)

    def test_happy_path(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        args, kwargs = self.start.call_args
        source, panels, compose = args[0], args[1], args[2]
        self.assertEqual(source.name, "src.png")
        self.assertTrue(compose)
        self.assertEqual(panels[0]["prompt"], "p")
        self.assertEqual(panels[0]["loras"][0][0], "x")
        self.assertIsNone(panels[0]["cached"])
        self.assertEqual(panels[1]["cached"].name, "front.png")
        self.assertEqual(kwargs["denoise"], 1.0)
        self.assertEqual(kwargs["workflow_dir"], app_module.COMFY_IMAGE2IMAGE_DIR)

    def test_missing_cached_file_is_regenerated(self):
        resp = self._post(panels=[{"key": "front", "prompt": "f", "cached": "/images/gone.png"}])
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(self.start.call_args.args[1][0]["cached"])

    def test_bad_cached_name_is_rejected(self):
        resp = self._post(panels=[{"key": "front", "prompt": "f", "cached": "/images/bad name.png"}])
        self.assertEqual(resp.status_code, 400)

    def test_unknown_and_duplicate_keys(self):
        self.assertEqual(self._post(panels=[{"key": "nope", "prompt": "p"}]).status_code, 400)
        dup = [{"key": "front", "prompt": "a"}, {"key": "front", "prompt": "b"}]
        self.assertEqual(self._post(panels=dup).status_code, 400)

    def test_empty_prompt_needs_cache(self):
        resp = self._post(panels=[{"key": "side", "prompt": "  "}])
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Side view", resp.get_json()["error"])
        ok = self._post(panels=[{"key": "front", "prompt": "", "cached": "/images/front.png"}])
        self.assertEqual(ok.status_code, 200)

    def test_no_panels_no_image_bad_workflow(self):
        self.assertEqual(self._post(panels=[]).status_code, 400)
        self.assertEqual(self._post(image="").status_code, 400)
        self.assertEqual(self._post(image="/images/missing.png").status_code, 404)
        self.assertEqual(self._post(workflow="other").status_code, 400)
        self.start.assert_not_called()


class SessionMediaTests(unittest.TestCase):
    def test_panels_count_as_chat_media(self):
        doc = {"sessionImages": ["/images/a.png"],
               "characterSheet": {"source": "/images/src.png",
                                  "results": {"front": "/images/front.png"}}}
        names = persistence.session_media_filenames(doc)
        self.assertEqual(names, {"a.png", "front.png"})


if __name__ == "__main__":
    unittest.main()
