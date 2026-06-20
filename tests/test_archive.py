import os
import sys
import json
import socket
import shutil
import tempfile
import threading
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import image_store as image_store_module
from app import app


class FakeAgent:
    """A throwaway Unix-socket server that emulates archive-agent. It
    records every request and replies {"ok": true} so the endpoint can exercise
    its mount -> copy -> unmount flow without zuluCrypt-cli."""

    def __init__(self, sock_path, mount_dir=None):
        self.sock_path = sock_path
        self.mount_dir = mount_dir
        # When True, mount succeeds but skips writing the marker — emulates a
        # MOUNT_DIR/bind-source mismatch where the volume never propagates in.
        self.skip_marker = False
        self.requests = []
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(sock_path)
        self._srv.listen(8)
        self._srv.settimeout(0.2)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            with conn:
                raw = b""
                while b"\n" not in raw:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    raw += chunk
                req = json.loads(raw.decode("utf-8").strip())
                self.requests.append(req)
                resp = {"ok": True}
                if req.get("action") == "mount":
                    resp["mountpoint"] = req.get("volume")
                    # Real agent drops a marker at the volume root on mount.
                    if self.mount_dir and not self.skip_marker:
                        with open(os.path.join(self.mount_dir, ".comfy-archive"),
                                  "w", encoding="utf-8") as fh:
                            fh.write("comfy-archive\n")
                conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)
        self._srv.close()


class TestArchive(unittest.TestCase):

    def setUp(self):
        app.testing = True
        self.client = app.test_client()
        self.tmp = tempfile.mkdtemp()

        self.images_dir = os.path.join(self.tmp, "output")
        self.mount_dir = os.path.join(self.tmp, "mnt")
        os.makedirs(self.images_dir)
        os.makedirs(self.mount_dir)

        self.sock_path = os.path.join(self.tmp, "agent.sock")
        self.agent = FakeAgent(self.sock_path, mount_dir=self.mount_dir)
        self.agent.start()

        # Point the app's module globals at our temp fixtures.
        from pathlib import Path
        self._orig = {
            "IMAGES_DIR": app_module.IMAGES_DIR,
            "ARCHIVE_VOLUME": app_module.ARCHIVE_VOLUME,
            "ARCHIVE_PASSWORD": app_module.ARCHIVE_PASSWORD,
            "ARCHIVE_AGENT_SOCKET": app_module.ARCHIVE_AGENT_SOCKET,
            "ARCHIVE_MOUNT_DIR": app_module.ARCHIVE_MOUNT_DIR,
        }
        self._orig_image_store_images_dir = image_store_module.IMAGES_DIR
        app_module.IMAGES_DIR = Path(self.images_dir)
        app_module.ARCHIVE_VOLUME = "/host/archive.img"
        app_module.ARCHIVE_PASSWORD = "s3cret"
        app_module.ARCHIVE_AGENT_SOCKET = self.sock_path
        app_module.ARCHIVE_MOUNT_DIR = Path(self.mount_dir)
        image_store_module.IMAGES_DIR = Path(self.images_dir)

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(app_module, k, v)
        image_store_module.IMAGES_DIR = self._orig_image_store_images_dir
        self.agent.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _auth(self):
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True

    def _make_image(self, name, days_old=0):
        path = os.path.join(self.images_dir, name)
        with open(path, "wb") as fh:
            fh.write(b"\x89PNG" + name.encode())
        if days_old:
            old = (datetime.now() - timedelta(days=days_old)).timestamp()
            os.utime(path, (old, old))
        return path

    def _staged_files(self):
        staging = os.path.join(self.mount_dir, "staging")
        found = []
        for root, _dirs, files in os.walk(staging):
            for f in files:
                found.append(f)
        return sorted(found)

    def _staging_dirs(self):
        staging = os.path.join(self.mount_dir, "staging")
        if not os.path.isdir(staging):
            return []
        return sorted(os.listdir(staging))

    def test_requires_auth(self):
        resp = self.client.post("/api/archive", json={"scope": "all"})
        self.assertEqual(resp.status_code, 302)

    def test_not_configured(self):
        self._auth()
        app_module.ARCHIVE_VOLUME = ""
        resp = self.client.post("/api/archive", json={"scope": "all"})
        self.assertEqual(resp.status_code, 503)

    def test_archive_all_moves_files(self):
        self._auth()
        self._make_image("a.png")
        self._make_image("b.png")
        resp = self.client.post("/api/archive", json={"scope": "all"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["archived"], 2)
        # Originals deleted (move semantics).
        self.assertEqual(os.listdir(self.images_dir), [])
        # Copies staged under staging/<guid>/.
        self.assertEqual(self._staged_files(), ["a.png", "b.png"])
        # Agent was asked to mount (with the password) and to unmount.
        actions = [r["action"] for r in self.agent.requests]
        self.assertEqual(actions, ["mount", "unmount"])
        mount_req = self.agent.requests[0]
        self.assertEqual(mount_req["volume"], "/host/archive.img")
        self.assertEqual(mount_req["password"], "s3cret")

    def test_archive_today_only(self):
        self._auth()
        self._make_image("today.png")
        self._make_image("old.png", days_old=3)
        resp = self.client.post("/api/archive", json={"scope": "today"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["archived"], 1)
        self.assertEqual(self._staged_files(), ["today.png"])
        # The old file is untouched.
        self.assertIn("old.png", os.listdir(self.images_dir))

    def test_archive_session_filenames(self):
        self._auth()
        self._make_image("keep.png")
        self._make_image("s1.png")
        resp = self.client.post(
            "/api/archive", json={"scope": "session", "filenames": ["s1.png"]}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["archived"], 1)
        self.assertEqual(self._staged_files(), ["s1.png"])
        self.assertIn("keep.png", os.listdir(self.images_dir))

    def test_archive_session_rejects_bad_filename(self):
        self._auth()
        resp = self.client.post(
            "/api/archive",
            json={"scope": "session", "filenames": ["../etc/passwd"]},
        )
        self.assertEqual(resp.status_code, 400)

    def test_archive_aborts_without_marker(self):
        # If the volume marker isn't visible the encrypted volume didn't mount
        # here (e.g. MOUNT_DIR/bind-source mismatch). The endpoint must refuse to
        # delete originals rather than silently writing to plain disk.
        self.agent.skip_marker = True
        self._auth()
        self._make_image("a.png")
        resp = self.client.post("/api/archive", json={"scope": "all"})
        self.assertEqual(resp.status_code, 500)
        # Original is preserved — no data loss.
        self.assertIn("a.png", os.listdir(self.images_dir))
        # The volume was still unmounted afterwards.
        actions = [r["action"] for r in self.agent.requests]
        self.assertEqual(actions, ["mount", "unmount"])

    def test_archive_name_slugified_into_folder(self):
        self._auth()
        self._make_image("a.png")
        resp = self.client.post(
            "/api/archive", json={"scope": "all", "name": "Man walking on Beach"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["folder"], "man-walking-on-beach")
        self.assertEqual(self._staging_dirs(), ["man-walking-on-beach"])
        self.assertEqual(self._staged_files(), ["a.png"])

    def test_archive_name_falls_back_to_guid(self):
        self._auth()
        self._make_image("a.png")
        # A name that slugifies to nothing usable falls back to a generated guid.
        resp = self.client.post(
            "/api/archive", json={"scope": "all", "name": "  !!! "}
        )
        self.assertEqual(resp.status_code, 200)
        folder = resp.get_json()["folder"]
        # 32-char hex guid, not the punctuation we sent.
        self.assertRegex(folder, r"^[0-9a-f]{32}$")
        self.assertEqual(self._staging_dirs(), [folder])

    def test_archive_no_name_uses_guid(self):
        self._auth()
        self._make_image("a.png")
        resp = self.client.post("/api/archive", json={"scope": "all"})
        self.assertEqual(resp.status_code, 200)
        self.assertRegex(resp.get_json()["folder"], r"^[0-9a-f]{32}$")

    def test_archive_empty_scope_noop(self):
        self._auth()
        resp = self.client.post("/api/archive", json={"scope": "all"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["archived"], 0)
        # Nothing to do means the agent is never contacted.
        self.assertEqual(self.agent.requests, [])


if __name__ == "__main__":
    unittest.main()
