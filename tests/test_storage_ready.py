"""Persistence endpoints must not answer from an unmounted output volume.

Macros, aliases, the default macro and saved chats all live in IMAGES_DIR, which
is the encrypted output volume's mountpoint. That volume mounts lazily on a
background thread after login, so there is a window in which the directory is an
empty stand-in. Reading it then reports "no macros" (which the UI cached for the
life of the page — the bug this guards against); writing then lands on the
container's writable layer and vanishes under the mount.
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import image_store as image_store_module
import persistence as persistence_module
from app import app


class TestStorageReadyGuard(unittest.TestCase):
    def setUp(self):
        app.testing = True
        self.client = app.test_client()
        self.tmp = tempfile.mkdtemp()
        self.images_dir = Path(self.tmp) / "output"
        self.images_dir.mkdir()
        self._orig = {
            (app_module, "IMAGES_DIR"): app_module.IMAGES_DIR,
            (app_module, "OUTPUT_VOLUME"): app_module.OUTPUT_VOLUME,
            (image_store_module, "IMAGES_DIR"): image_store_module.IMAGES_DIR,
            (image_store_module, "OUTPUT_VOLUME"): image_store_module.OUTPUT_VOLUME,
            (persistence_module, "IMAGES_DIR"): persistence_module.IMAGES_DIR,
        }
        app_module.IMAGES_DIR = self.images_dir
        image_store_module.IMAGES_DIR = self.images_dir
        persistence_module.IMAGES_DIR = self.images_dir
        app_module.OUTPUT_VOLUME = "/host/output.luks"
        image_store_module.OUTPUT_VOLUME = "/host/output.luks"

    def tearDown(self):
        for (mod, name), value in self._orig.items():
            setattr(mod, name, value)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _auth(self):
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True

    def _mount(self):
        (self.images_dir / app_module.OUTPUT_MARKER).write_text("comfy-archive\n")

    # -- reads -------------------------------------------------------------

    def test_reads_refused_while_unmounted(self):
        self._auth()
        for path in ("/api/macros", "/api/aliases", "/api/default-macro",
                     "/api/chats", "/api/images"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 503)

    def test_reads_allowed_once_mounted(self):
        self._auth()
        self._mount()
        (self.images_dir / "macros.json").write_text(json.dumps({"m": ["a step"]}))
        self.assertEqual(self.client.get("/api/macros").json, {"m": ["a step"]})
        self.assertEqual(self.client.get("/api/aliases").status_code, 200)
        self.assertEqual(self.client.get("/api/chats").status_code, 200)

    def test_reads_allowed_when_encryption_disabled(self):
        self._auth()
        app_module.OUTPUT_VOLUME = ""
        image_store_module.OUTPUT_VOLUME = ""
        self.assertEqual(self.client.get("/api/macros").status_code, 200)

    # -- writes ------------------------------------------------------------

    def test_writes_refused_while_unmounted(self):
        self._auth()
        resp = self.client.post("/api/macros", json={"name": "m", "steps": ["a"]})
        self.assertEqual(resp.status_code, 503)
        # Nothing was written to the (unmounted) stand-in directory.
        self.assertFalse((self.images_dir / "macros.json").exists())
        self.assertEqual(
            self.client.post("/api/aliases", json={"from": "a", "to": "b"}).status_code,
            503)
        self.assertEqual(
            self.client.post("/api/chats", json={"name": "c"}).status_code, 503)

    # -- readiness probe ---------------------------------------------------

    def test_storage_status_reports_not_ready_then_ready(self):
        self._auth()
        first = self.client.get("/api/storage-status").json
        self.assertEqual(first, {"encrypted": True, "ready": False})
        self._mount()
        self.assertEqual(self.client.get("/api/storage-status").json,
                         {"encrypted": True, "ready": True})

    def test_storage_status_ready_when_encryption_disabled(self):
        self._auth()
        app_module.OUTPUT_VOLUME = ""
        image_store_module.OUTPUT_VOLUME = ""
        self.assertEqual(self.client.get("/api/storage-status").json,
                         {"encrypted": False, "ready": True})

    def test_storage_status_requires_login(self):
        self.assertEqual(self.client.get("/api/storage-status").status_code, 302)


if __name__ == "__main__":
    unittest.main()
