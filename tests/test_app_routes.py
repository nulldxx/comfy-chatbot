"""Tests for app.py routes not covered by other test files.

Covers: catalogue endpoints, image management, session/alias CRUD, upload-mask
endpoints, and generation-route validation (with start_generation_job mocked
so no ComfyUI connection is needed).
"""
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import image_store as image_store_module
import persistence as persistence_module
from app import app


def _auth(client):
    with client.session_transaction() as sess:
        sess["authenticated"] = True


class _AppFixture(unittest.TestCase):
    """Base class: authenticated client + temp IMAGES_DIR."""

    def setUp(self):
        app.testing = True
        self.client = app.test_client()
        _auth(self.client)
        self.tmp = tempfile.mkdtemp()
        self.images_dir = Path(self.tmp) / "images"
        self.images_dir.mkdir()
        self._patch("IMAGES_DIR", self.images_dir)
        image_store_module.IMAGES_DIR = self.images_dir

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        image_store_module.IMAGES_DIR = app_module.__dict__.get(
            "IMAGES_DIR", self.images_dir
        )
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patchers(self):
        return []

    def setUp(self):
        app.testing = True
        self.client = app.test_client()
        _auth(self.client)
        self.tmp = tempfile.mkdtemp()
        self.images_dir = Path(self.tmp) / "images"
        self.images_dir.mkdir()
        self._patcher_images = patch.object(app_module, "IMAGES_DIR", self.images_dir)
        self._patcher_images.start()
        self._patcher_images_store = patch.object(
            image_store_module, "IMAGES_DIR", self.images_dir
        )
        self._patcher_images_store.start()
        self._patcher_pers = patch.object(
            persistence_module, "IMAGES_DIR", self.images_dir
        )
        self._patcher_pers.start()

    def tearDown(self):
        self._patcher_images.stop()
        self._patcher_images_store.stop()
        self._patcher_pers.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_image(self, name="test.png"):
        p = self.images_dir / name
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)
        return p

    def _patch(self, attr, val, module=None):
        m = module or app_module
        p = patch.object(m, attr, val)
        p.start()
        return p


# ---------------------------------------------------------------------------
# Catalogue endpoints
# ---------------------------------------------------------------------------

class TestCatalogueEndpoints(_AppFixture):
    def test_api_servers_returns_default(self):
        resp = self.client.get("/api/servers")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

    def test_api_workflows_returns_list(self):
        resp = self.client.get("/api/workflows")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_api_facedetailer_workflows_returns_list(self):
        resp = self.client.get("/api/facedetailer-workflows")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_api_upscaler_workflows_returns_list(self):
        resp = self.client.get("/api/upscaler-workflows")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_api_image2image_workflows_returns_list(self):
        resp = self.client.get("/api/image2image-workflows")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_api_inpainting_workflows_returns_list(self):
        resp = self.client.get("/api/inpainting-workflows")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)


class TestAddServer(_AppFixture):
    def setUp(self):
        super().setUp()
        self.wf_dir = Path(self.tmp) / "workflows"
        self.wf_dir.mkdir()
        self._wf_patcher = patch.object(app_module, "COMFY_WORKFLOW_DIR", self.wf_dir)
        self._wf_patcher.start()

    def tearDown(self):
        self._wf_patcher.stop()
        super().tearDown()

    def _post(self, **kwargs):
        payload = {"name": "srv", "host": "10.0.0.1", "port": 8188, "os": "unix"}
        payload.update(kwargs)
        return self.client.post("/api/add-server", json=payload)

    def test_valid_server_returns_entry(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["name"], "srv")
        self.assertEqual(data["host"], "10.0.0.1")

    def test_missing_name_returns_400(self):
        resp = self._post(name="")
        self.assertEqual(resp.status_code, 400)

    def test_missing_host_returns_400(self):
        resp = self._post(host="")
        self.assertEqual(resp.status_code, 400)

    def test_invalid_port_returns_400(self):
        resp = self._post(port="not-a-number")
        self.assertEqual(resp.status_code, 400)

    def test_port_out_of_range_returns_400(self):
        resp = self._post(port=99999)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_os_returns_400(self):
        resp = self._post(os="beos")
        self.assertEqual(resp.status_code, 400)

    def test_saves_servers_json(self):
        self._post()
        servers_file = self.wf_dir / "servers.json"
        self.assertTrue(servers_file.is_file())
        data = json.loads(servers_file.read_text())
        self.assertEqual(data["servers"][0]["name"], "srv")


# ---------------------------------------------------------------------------
# Image management
# ---------------------------------------------------------------------------

class TestImageEndpoints(_AppFixture):
    def test_api_images_returns_list(self):
        self._make_image("a.png")
        self._make_image("b.png")
        resp = self.client.get("/api/images")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data), 2)

    def test_api_images_today_filter(self):
        self._make_image("today.png")
        resp = self.client.get("/api/images?filter=today")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.get_json()), 1)

    def test_delete_image(self):
        self._make_image("del.png")
        resp = self.client.delete("/api/images/del.png")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["deleted"], "del.png")
        self.assertFalse((self.images_dir / "del.png").is_file())

    def test_delete_image_not_found(self):
        resp = self.client.delete("/api/images/ghost.png")
        self.assertEqual(resp.status_code, 404)

    def test_delete_image_invalid_filename(self):
        # "%20" decodes to a space; secure_filename changes it → 400
        resp = self.client.delete("/api/images/my%20file.png")
        self.assertEqual(resp.status_code, 400)

    def test_delete_all_images(self):
        self._make_image("x.png")
        self._make_image("y.png")
        resp = self.client.delete("/api/images")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["deleted"], 2)
        self.assertEqual(list(self.images_dir.glob("*.png")), [])

    def test_serve_image(self):
        self._make_image("pic.png")
        resp = self.client.get("/images/pic.png")
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Upload endpoints
# ---------------------------------------------------------------------------

class TestUploadMask(_AppFixture):
    def setUp(self):
        super().setUp()
        masks_dir = self.images_dir / ".masks"
        masks_dir.mkdir(exist_ok=True)
        self._masks_patcher = patch.object(image_store_module, "MASKS_DIR", masks_dir)
        self._masks_patcher.start()

    def tearDown(self):
        self._masks_patcher.stop()
        super().tearDown()

    def _b64(self, data=b"\x89PNG"):
        return base64.b64encode(data).decode()

    def test_valid_mask_returns_token(self):
        resp = self.client.post("/api/upload-mask", json={"data": self._b64()})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("token", resp.get_json())

    def test_missing_data_returns_400(self):
        resp = self.client.post("/api/upload-mask", json={})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_base64_returns_400(self):
        resp = self.client.post("/api/upload-mask", json={"data": "!!!notbase64!!!"})
        self.assertEqual(resp.status_code, 400)


class TestUploadInpaintImage(_AppFixture):
    def setUp(self):
        super().setUp()
        inpaint_dir = self.images_dir / ".inpaint-inputs"
        inpaint_dir.mkdir(exist_ok=True)
        self._inp_patcher = patch.object(
            image_store_module, "INPAINT_INPUTS_DIR", inpaint_dir
        )
        self._inp_patcher.start()

    def tearDown(self):
        self._inp_patcher.stop()
        super().tearDown()

    def test_valid_image_returns_token(self):
        b64 = base64.b64encode(b"\x89PNG").decode()
        resp = self.client.post("/api/upload-inpaint-image", json={"data": b64})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("token", resp.get_json())

    def test_missing_data_returns_400(self):
        resp = self.client.post("/api/upload-inpaint-image", json={})
        self.assertEqual(resp.status_code, 400)


class TestSaveImage(_AppFixture):
    def test_valid_image_saved(self):
        b64 = base64.b64encode(b"\x89PNG").decode()
        resp = self.client.post("/api/save-image", json={"data": b64})
        self.assertEqual(resp.status_code, 200)
        url = resp.get_json()["url"]
        self.assertTrue(url.startswith("/images/"))

    def test_missing_data_returns_400(self):
        resp = self.client.post("/api/save-image", json={})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_base64_returns_400(self):
        # "A" is invalid padding (1 char is not a multiple of 4) → raises
        resp = self.client.post("/api/save-image", json={"data": "A"})
        self.assertEqual(resp.status_code, 400)


class TestImportImage(_AppFixture):
    def test_valid_import(self):
        resp = self.client.post(
            "/api/import-image",
            data={"file": (io.BytesIO(b"\x89PNG\x00\x00"), "photo.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("/images/", resp.get_json()["url"])

    def test_no_file_returns_400(self):
        resp = self.client.post("/api/import-image", data={})
        self.assertEqual(resp.status_code, 400)

    def test_unsupported_extension_returns_400(self):
        resp = self.client.post(
            "/api/import-image",
            data={"file": (io.BytesIO(b"data"), "doc.pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)

    def test_empty_file_returns_400(self):
        resp = self.client.post(
            "/api/import-image",
            data={"file": (io.BytesIO(b""), "empty.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)


class TestExtractLastFrame(_AppFixture):
    def _make_video(self, name="clip.mp4", seconds=1):
        """Render a tiny real test clip with ffmpeg into IMAGES_DIR."""
        path = self.images_dir / name
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-y", "-f", "lavfi",
                "-i", f"testsrc=duration={seconds}:size=64x64:rate=10",
                "-pix_fmt", "yuv420p", str(path),
            ],
            capture_output=True, check=True,
        )
        return path

    def test_missing_url_returns_400(self):
        resp = self.client.post("/api/extract-last-frame", json={})
        self.assertEqual(resp.status_code, 400)

    def test_non_video_returns_400(self):
        self._make_image("still.png")
        resp = self.client.post(
            "/api/extract-last-frame", json={"url": "/images/still.png"}
        )
        self.assertEqual(resp.status_code, 400)

    def test_unsafe_filename_returns_400(self):
        # A name secure_filename would rewrite (space) is rejected outright.
        resp = self.client.post(
            "/api/extract-last-frame", json={"url": "/images/my clip.mp4"}
        )
        self.assertEqual(resp.status_code, 400)

    def test_traversal_url_reduced_to_basename(self):
        # A path-traversal URL collapses to its basename, so it never escapes
        # IMAGES_DIR — here it just resolves to a non-existent file.
        resp = self.client.post(
            "/api/extract-last-frame", json={"url": "/images/../etc/passwd.mp4"}
        )
        self.assertEqual(resp.status_code, 404)

    def test_video_not_found_returns_404(self):
        resp = self.client.post(
            "/api/extract-last-frame", json={"url": "/images/missing.mp4"}
        )
        self.assertEqual(resp.status_code, 404)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_extracts_last_frame(self):
        self._make_video("clip.mp4")
        resp = self.client.post(
            "/api/extract-last-frame", json={"url": "/images/clip.mp4"}
        )
        self.assertEqual(resp.status_code, 200)
        out_url = resp.get_json()["url"]
        self.assertTrue(out_url.startswith("/images/"))
        self.assertTrue(out_url.endswith(".png"))
        out_path = self.images_dir / out_url.rsplit("/", 1)[-1]
        self.assertTrue(out_path.is_file())
        # PNG magic number — the extracted frame is a real image.
        self.assertEqual(out_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")


class TestCompositeVideos(_AppFixture):
    def _make_video(self, name, seconds=1):
        """Render a tiny real test clip with ffmpeg into IMAGES_DIR."""
        path = self.images_dir / name
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-y", "-f", "lavfi",
                "-i", f"testsrc=duration={seconds}:size=64x64:rate=10",
                "-pix_fmt", "yuv420p", str(path),
            ],
            capture_output=True, check=True,
        )
        return path

    def _make_video_with_audio(self, name, seconds=1):
        """Render a tiny real test clip that carries an audio (sine) track."""
        path = self.images_dir / name
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-y",
                "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=64x64:rate=10",
                "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
            ],
            capture_output=True, check=True,
        )
        return path

    @staticmethod
    def _has_audio_stream(path):
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "a",
                "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path),
            ],
            capture_output=True,
        )
        return b"audio" in proc.stdout

    def test_fewer_than_two_returns_400(self):
        resp = self.client.post(
            "/api/composite-videos", json={"urls": ["/images/clip.mp4"]}
        )
        self.assertEqual(resp.status_code, 400)

    def test_non_video_returns_400(self):
        self._make_image("still.png")
        resp = self.client.post(
            "/api/composite-videos",
            json={"urls": ["/images/still.png", "/images/still.png"]},
        )
        self.assertEqual(resp.status_code, 400)

    def test_unsafe_filename_returns_400(self):
        resp = self.client.post(
            "/api/composite-videos",
            json={"urls": ["/images/my clip.mp4", "/images/other.mp4"]},
        )
        self.assertEqual(resp.status_code, 400)

    def test_video_not_found_returns_404(self):
        resp = self.client.post(
            "/api/composite-videos",
            json={"urls": ["/images/missing.mp4", "/images/gone.mp4"]},
        )
        self.assertEqual(resp.status_code, 404)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_composites_videos(self):
        self._make_video("a.mp4")
        self._make_video("b.mp4")
        resp = self.client.post(
            "/api/composite-videos",
            json={"urls": ["/images/a.mp4", "/images/b.mp4"]},
        )
        self.assertEqual(resp.status_code, 200)
        out_url = resp.get_json()["url"]
        self.assertTrue(out_url.startswith("/images/"))
        self.assertTrue(out_url.endswith(".mp4"))
        out_path = self.images_dir / out_url.rsplit("/", 1)[-1]
        self.assertTrue(out_path.is_file())
        self.assertGreater(out_path.stat().st_size, 0)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_composite_preserves_audio(self):
        self._make_video_with_audio("a.mp4")
        self._make_video_with_audio("b.mp4")
        resp = self.client.post(
            "/api/composite-videos",
            json={"urls": ["/images/a.mp4", "/images/b.mp4"]},
        )
        self.assertEqual(resp.status_code, 200)
        out_path = self.images_dir / resp.get_json()["url"].rsplit("/", 1)[-1]
        self.assertTrue(out_path.is_file())
        self.assertTrue(self._has_audio_stream(out_path))

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_composite_mixed_audio_and_silent(self):
        # One clip with audio, one without: the silent clip is backed by
        # generated silence so the joined output still carries an audio track.
        self._make_video_with_audio("a.mp4")
        self._make_video("b.mp4")
        resp = self.client.post(
            "/api/composite-videos",
            json={"urls": ["/images/a.mp4", "/images/b.mp4"]},
        )
        self.assertEqual(resp.status_code, 200)
        out_path = self.images_dir / resp.get_json()["url"].rsplit("/", 1)[-1]
        self.assertTrue(self._has_audio_stream(out_path))


# ---------------------------------------------------------------------------
# Generation route validation (mocked job launcher)
# ---------------------------------------------------------------------------

class TestGenerateValidation(_AppFixture):
    def setUp(self):
        super().setUp()
        self._gen_patcher = patch(
            "app.start_generation_job", return_value="fake-job-id"
        )
        self._gen_patcher.start()
        # Disable output-volume guard
        self._vol_patcher = patch.object(app_module, "OUTPUT_VOLUME", "")
        self._vol_patcher.start()
        self._vol_patcher2 = patch.object(image_store_module, "OUTPUT_VOLUME", "")
        self._vol_patcher2.start()

    def tearDown(self):
        self._gen_patcher.stop()
        self._vol_patcher.stop()
        self._vol_patcher2.stop()
        super().tearDown()

    def test_valid_generate_returns_job_id(self):
        resp = self.client.post("/api/generate", json={"prompt": "a cat"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["job_id"], "fake-job-id")

    def test_empty_prompt_after_lora_returns_400(self):
        resp = self.client.post("/api/generate", json={"prompt": "<lora:x:1.0>"})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_width_returns_400(self):
        resp = self.client.post("/api/generate", json={"prompt": "cat", "width": "nope"})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_height_returns_400(self):
        resp = self.client.post("/api/generate", json={"prompt": "cat", "height": "bad"})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_steps_returns_400(self):
        resp = self.client.post("/api/generate", json={"prompt": "cat", "steps": 0})
        self.assertEqual(resp.status_code, 400)

    def test_steps_not_integer_returns_400(self):
        resp = self.client.post("/api/generate", json={"prompt": "cat", "steps": "lots"})
        self.assertEqual(resp.status_code, 400)


class TestParseDenoise(_AppFixture):
    """_parse_denoise is called by face-detail, upscale, image2image, inpaint."""

    def setUp(self):
        super().setUp()
        self._img = self._make_image("src.png")
        self._gen_patcher = patch("app.start_generation_job", return_value="j")
        self._gen_patcher.start()
        self._vol_patcher = patch.object(app_module, "OUTPUT_VOLUME", "")
        self._vol_patcher.start()
        self._vol_patcher2 = patch.object(image_store_module, "OUTPUT_VOLUME", "")
        self._vol_patcher2.start()
        import catalogue
        # Make list_upscaler_workflows return one entry without touching the filesystem
        self._wf_patcher = patch.object(catalogue, "COMFY_UPSCALER_DIR",
                                        Path(self.tmp) / "upscaler")
        Path(self.tmp, "upscaler").mkdir()
        (Path(self.tmp, "upscaler") / "upscaler.json").write_text("{}")
        self._wf_patcher.start()

    def tearDown(self):
        self._gen_patcher.stop()
        self._vol_patcher.stop()
        self._vol_patcher2.stop()
        self._wf_patcher.stop()
        super().tearDown()

    def test_valid_denoise_accepted(self):
        resp = self.client.post(
            "/api/upscale", json={"image": "/images/src.png", "denoise": 0.75}
        )
        self.assertEqual(resp.status_code, 200)

    def test_denoise_out_of_range_returns_400(self):
        resp = self.client.post(
            "/api/upscale", json={"image": "/images/src.png", "denoise": 1.5}
        )
        self.assertEqual(resp.status_code, 400)

    def test_denoise_not_a_number_returns_400(self):
        resp = self.client.post(
            "/api/upscale", json={"image": "/images/src.png", "denoise": "lots"}
        )
        self.assertEqual(resp.status_code, 400)


class TestImage2VideoSettings(_AppFixture):
    """/api/image2video accepts duration/frames/fps for the <DURATION>/<FRAMES>/<FPS> slots."""

    def setUp(self):
        super().setUp()
        self._img = self._make_image("src.png")
        self._gen_patcher = patch("app.start_generation_job", return_value="j")
        self._gen = self._gen_patcher.start()
        self._vol_patcher = patch.object(app_module, "OUTPUT_VOLUME", "")
        self._vol_patcher.start()
        self._vol_patcher2 = patch.object(image_store_module, "OUTPUT_VOLUME", "")
        self._vol_patcher2.start()
        import catalogue
        self._wf_patcher = patch.object(catalogue, "COMFY_IMAGE2VIDEO_DIR",
                                        Path(self.tmp) / "image2video")
        Path(self.tmp, "image2video").mkdir()
        (Path(self.tmp, "image2video") / "vid.json").write_text("{}")
        self._wf_patcher.start()

    def tearDown(self):
        self._gen_patcher.stop()
        self._vol_patcher.stop()
        self._vol_patcher2.stop()
        self._wf_patcher.stop()
        super().tearDown()

    def test_valid_settings_forwarded(self):
        resp = self.client.post(
            "/api/image2video",
            json={"image": "/images/src.png", "workflow": "vid",
                  "duration": 5, "frames": 125, "fps": 25},
        )
        self.assertEqual(resp.status_code, 200)
        _, kwargs = self._gen.call_args
        self.assertEqual(kwargs["duration"], 5.0)
        self.assertEqual(kwargs["frames"], 125)
        self.assertEqual(kwargs["fps"], 25)

    def test_negative_duration_returns_400(self):
        resp = self.client.post(
            "/api/image2video",
            json={"image": "/images/src.png", "workflow": "vid", "duration": -1},
        )
        self.assertEqual(resp.status_code, 400)

    def test_fps_not_integer_returns_400(self):
        resp = self.client.post(
            "/api/image2video",
            json={"image": "/images/src.png", "workflow": "vid", "fps": "lots"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_settings_optional(self):
        resp = self.client.post(
            "/api/image2video",
            json={"image": "/images/src.png", "workflow": "vid"},
        )
        self.assertEqual(resp.status_code, 200)

    def test_no_last_frame_forwards_none(self):
        resp = self.client.post(
            "/api/image2video",
            json={"image": "/images/src.png", "workflow": "vid"},
        )
        self.assertEqual(resp.status_code, 200)
        _, kwargs = self._gen.call_args
        self.assertIsNone(kwargs["input_last_frame"])

    def test_last_frame_resolved_and_forwarded(self):
        self._make_image("end.png")
        resp = self.client.post(
            "/api/image2video",
            json={"image": "/images/src.png", "workflow": "vid",
                  "last_frame": "/images/end.png"},
        )
        self.assertEqual(resp.status_code, 200)
        _, kwargs = self._gen.call_args
        self.assertEqual(Path(kwargs["input_last_frame"]).name, "end.png")

    def test_missing_last_frame_returns_404(self):
        resp = self.client.post(
            "/api/image2video",
            json={"image": "/images/src.png", "workflow": "vid",
                  "last_frame": "/images/nope.png"},
        )
        self.assertEqual(resp.status_code, 404)


class TestSequenceRoutes(_AppFixture):
    """/api/sequence and /api/video-sequence start a cancellable Grok job and
    return a job_id; the slow Grok call runs in the job (see test_generation_service)."""

    def setUp(self):
        super().setUp()
        self._job_patcher = patch.object(
            app_module, "start_sequence_job", return_value="seq-job-id"
        )
        self._job = self._job_patcher.start()

    def tearDown(self):
        self._job_patcher.stop()
        super().tearDown()

    def test_sequence_requires_master_prompt(self):
        resp = self.client.post("/api/sequence", json={"prompt": "  "})
        self.assertEqual(resp.status_code, 400)
        self._job.assert_not_called()

    def test_video_sequence_requires_master_prompt(self):
        resp = self.client.post("/api/video-sequence", json={"prompt": "  "})
        self.assertEqual(resp.status_code, 400)
        self._job.assert_not_called()

    def test_sequence_returns_job_id(self):
        resp = self.client.post(
            "/api/sequence",
            json={"prompt": "pets", "count": 2, "replacements": [["cat", "dog"]]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["job_id"], "seq-job-id")
        self._job.assert_called_once_with("pets", 2, [("cat", "dog")], video=False)

    def test_video_sequence_returns_job_id(self):
        resp = self.client.post("/api/video-sequence", json={"prompt": "pets", "count": 3})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["job_id"], "seq-job-id")
        self._job.assert_called_once_with("pets", 3, [], video=True)

    def test_count_is_clamped(self):
        self.client.post("/api/sequence", json={"prompt": "x", "count": 999})
        self.assertEqual(self._job.call_args[0][1], 64)


class TestSequenceRunRoute(_AppFixture):
    """/api/sequence-run drives the whole run server-side; the route validates
    input and starts the job (mocked here — the loop is in test_generation_service)."""

    def setUp(self):
        super().setUp()
        self._job_patcher = patch.object(
            app_module, "start_sequence_run_job", return_value="run-job-id"
        )
        self._job = self._job_patcher.start()

    def tearDown(self):
        self._job_patcher.stop()
        super().tearDown()

    def test_returns_job_id(self):
        resp = self.client.post("/api/sequence-run", json={
            "prompt": "pets", "count": 2, "recordingName": "My Run",
            "settings": {"workflow": "wf", "width": 512, "height": 512},
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["job_id"], "run-job-id")
        args = self._job.call_args[0]
        # (master, count, replacements, video, recording_name, gen_settings)
        self.assertEqual(args[0], "pets")
        self.assertEqual(args[3], False)
        self.assertEqual(args[4], "my-run")  # slugified
        self.assertEqual(args[5]["width"], 512)

    def test_requires_recording_name(self):
        resp = self.client.post("/api/sequence-run", json={"prompt": "pets"})
        self.assertEqual(resp.status_code, 400)
        self._job.assert_not_called()

    def test_requires_master_prompt(self):
        resp = self.client.post("/api/sequence-run", json={"prompt": "  ", "recordingName": "r"})
        self.assertEqual(resp.status_code, 400)
        self._job.assert_not_called()

    def test_video_flag_passed_through(self):
        self.client.post("/api/sequence-run", json={
            "prompt": "pets", "recordingName": "r", "video": True,
        })
        self.assertTrue(self._job.call_args[0][3])

    def test_bad_width_returns_400(self):
        resp = self.client.post("/api/sequence-run", json={
            "prompt": "pets", "recordingName": "r", "settings": {"width": "big"},
        })
        self.assertEqual(resp.status_code, 400)
        self._job.assert_not_called()

    def test_extra_prompt_passed_through(self):
        self.client.post("/api/sequence-run", json={
            "prompt": "pets", "recordingName": "r",
            "settings": {"extraPrompt": "  in the style of monet  "},
        })
        gen_settings = self._job.call_args[0][5]
        self.assertEqual(gen_settings["extraPrompt"], "in the style of monet")

    def test_extra_prompt_absent_is_none(self):
        self.client.post("/api/sequence-run", json={"prompt": "pets", "recordingName": "r"})
        gen_settings = self._job.call_args[0][5]
        self.assertIsNone(gen_settings["extraPrompt"])


# ---------------------------------------------------------------------------
# Session endpoints
# ---------------------------------------------------------------------------

class TestChatEndpoints(_AppFixture):
    def test_list_chats_empty(self):
        resp = self.client.get("/api/chats")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [])

    def test_chat_save(self):
        resp = self.client.post(
            "/api/chats", json={"name": "My Chat", "messages": []}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["name"], "my-chat")

    def test_chat_save_bad_name_returns_400(self):
        resp = self.client.post("/api/chats", json={"name": "!!! ###"})
        self.assertEqual(resp.status_code, 400)

    def test_load_chat(self):
        self.client.post("/api/chats", json={"name": "mychat", "messages": []})
        resp = self.client.get("/api/chats/mychat")
        self.assertEqual(resp.status_code, 200)

    def test_load_chat_not_found(self):
        resp = self.client.get("/api/chats/ghost")
        self.assertEqual(resp.status_code, 404)

    def test_load_chat_invalid_name(self):
        # "my%20chat" decodes to "my chat"; secure_filename changes it → 400
        resp = self.client.get("/api/chats/my%20chat")
        self.assertEqual(resp.status_code, 400)

    def test_delete_chat(self):
        self.client.post("/api/chats", json={"name": "todel", "messages": []})
        resp = self.client.delete("/api/chats/todel")
        self.assertEqual(resp.status_code, 200)

    def test_delete_chat_not_found(self):
        resp = self.client.delete("/api/chats/nope")
        self.assertEqual(resp.status_code, 404)

    def test_chat_rename(self):
        self.client.post("/api/chats", json={"name": "temp-1", "messages": []})
        resp = self.client.post("/api/chats/rename", json={"from": "temp-1", "to": "Sunsets"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["name"], "sunsets")
        # old gone, new present
        self.assertEqual(self.client.get("/api/chats/temp-1").status_code, 404)
        self.assertEqual(self.client.get("/api/chats/sunsets").status_code, 200)

    def test_chat_rename_missing_names_returns_400(self):
        resp = self.client.post("/api/chats/rename", json={"from": "temp-1"})
        self.assertEqual(resp.status_code, 400)

    def test_chat_rename_to_existing_returns_409(self):
        self.client.post("/api/chats", json={"name": "a", "messages": []})
        self.client.post("/api/chats", json={"name": "b", "messages": []})
        resp = self.client.post("/api/chats/rename", json={"from": "a", "to": "b"})
        self.assertEqual(resp.status_code, 409)

    def test_chat_rename_missing_source_ok_when_not_yet_written(self):
        # A temp chat with no images/save yet has no file; rename still succeeds
        # (live jobs are retargeted regardless).
        resp = self.client.post("/api/chats/rename", json={"from": "temp-x", "to": "named"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["name"], "named")


# ---------------------------------------------------------------------------
# Alias endpoints
# ---------------------------------------------------------------------------

class TestAliasEndpoints(_AppFixture):
    def test_list_aliases_empty(self):
        resp = self.client.get("/api/aliases")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {})

    def test_create_alias(self):
        resp = self.client.post(
            "/api/aliases", json={"from": "cat", "to": "a fluffy cat on a beach"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["updated"])

    def test_create_alias_missing_from_returns_400(self):
        resp = self.client.post("/api/aliases", json={"from": "", "to": "something"})
        self.assertEqual(resp.status_code, 400)

    def test_create_alias_missing_to_returns_400(self):
        resp = self.client.post("/api/aliases", json={"from": "x", "to": ""})
        self.assertEqual(resp.status_code, 400)

    def test_create_alias_with_space_returns_400(self):
        resp = self.client.post(
            "/api/aliases", json={"from": "my alias", "to": "something"}
        )
        self.assertEqual(resp.status_code, 400)

    def test_update_existing_alias(self):
        self.client.post("/api/aliases", json={"from": "x", "to": "old"})
        resp = self.client.post("/api/aliases", json={"from": "x", "to": "new"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["updated"])

    def test_delete_alias(self):
        self.client.post("/api/aliases", json={"from": "del", "to": "val"})
        resp = self.client.delete("/api/aliases/del")
        self.assertEqual(resp.status_code, 200)

    def test_delete_alias_not_found(self):
        resp = self.client.delete("/api/aliases/nope")
        self.assertEqual(resp.status_code, 404)

    def test_list_aliases_after_create(self):
        self.client.post("/api/aliases", json={"from": "cat", "to": "a cat"})
        resp = self.client.get("/api/aliases")
        self.assertEqual(resp.get_json(), {"cat": "a cat"})


if __name__ == "__main__":
    unittest.main()
