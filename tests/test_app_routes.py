"""Tests for app.py routes not covered by other test files.

Covers: catalogue endpoints, image management, session/alias CRUD, upload
endpoints, and generation-route validation (with start_generation_job mocked
so no ComfyUI connection is needed).
"""
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


class TestUploadWorkflow(_AppFixture):
    def setUp(self):
        super().setUp()
        self.wf_dir = Path(self.tmp) / "workflows" / "generation"
        self.wf_dir.mkdir(parents=True)
        self._wf_patcher = patch.object(app_module, "COMFY_GENERATION_DIR", self.wf_dir)
        self._wf_patcher.start()

    def tearDown(self):
        self._wf_patcher.stop()
        super().tearDown()

    def test_valid_workflow_saved(self):
        content = json.dumps({"1": {"inputs": {"prompt": "<PROMPT>"}}}).encode()
        resp = self.client.post(
            "/api/upload-workflow",
            data={"file": (io.BytesIO(content), "my_workflow.json")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["name"], "my_workflow")
        self.assertTrue((self.wf_dir / "my_workflow.json").is_file())

    def test_no_file_returns_400(self):
        resp = self.client.post("/api/upload-workflow", data={})
        self.assertEqual(resp.status_code, 400)

    def test_non_json_file_returns_400(self):
        resp = self.client.post(
            "/api/upload-workflow",
            data={"file": (io.BytesIO(b"data"), "image.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_json_content_returns_400(self):
        resp = self.client.post(
            "/api/upload-workflow",
            data={"file": (io.BytesIO(b"not json"), "wf.json")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)


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


# ---------------------------------------------------------------------------
# Session endpoints
# ---------------------------------------------------------------------------

class TestSessionEndpoints(_AppFixture):
    def test_list_sessions_empty(self):
        resp = self.client.get("/api/sessions")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [])

    def test_save_session(self):
        resp = self.client.post(
            "/api/sessions", json={"name": "My Session", "messages": []}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["name"], "my-session")

    def test_save_session_bad_name_returns_400(self):
        resp = self.client.post("/api/sessions", json={"name": "!!! ###"})
        self.assertEqual(resp.status_code, 400)

    def test_load_session(self):
        self.client.post("/api/sessions", json={"name": "mysession", "messages": []})
        resp = self.client.get("/api/sessions/mysession")
        self.assertEqual(resp.status_code, 200)

    def test_load_session_not_found(self):
        resp = self.client.get("/api/sessions/ghost")
        self.assertEqual(resp.status_code, 404)

    def test_load_session_invalid_name(self):
        # "my%20session" decodes to "my session"; secure_filename changes it → 400
        resp = self.client.get("/api/sessions/my%20session")
        self.assertEqual(resp.status_code, 400)

    def test_delete_session(self):
        self.client.post("/api/sessions", json={"name": "todel", "messages": []})
        resp = self.client.delete("/api/sessions/todel")
        self.assertEqual(resp.status_code, 200)

    def test_delete_session_not_found(self):
        resp = self.client.delete("/api/sessions/nope")
        self.assertEqual(resp.status_code, 404)


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
