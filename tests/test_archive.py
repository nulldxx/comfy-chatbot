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
        # Reported by the "status" action (and gated on by the app's archive/fsck
        # exclusive-mode guard): True emulates the host having the volume mounted
        # via `m`.
        self.host_mounted = False
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
                action = req.get("action")
                if action == "mount":
                    resp["mountpoint"] = req.get("volume")
                    # Real agent drops a marker at the volume root on mount.
                    if self.mount_dir and not self.skip_marker:
                        with open(os.path.join(self.mount_dir, ".comfy-archive"),
                                  "w", encoding="utf-8") as fh:
                            fh.write("comfy-archive\n")
                elif action == "host-mount":
                    resp["mountpoint"] = "/run/media/private/ben/secure"
                elif action == "status":
                    resp["host_mounted"] = self.host_mounted
                    resp["open"] = self.host_mounted
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
            "SECRET_KEY": app_module.SECRET_KEY,
            "ARCHIVE_AGENT_SOCKET": app_module.ARCHIVE_AGENT_SOCKET,
            "ARCHIVE_MOUNT_DIR": app_module.ARCHIVE_MOUNT_DIR,
        }
        self._orig_image_store_images_dir = image_store_module.IMAGES_DIR
        app_module.IMAGES_DIR = Path(self.images_dir)
        app_module.ARCHIVE_VOLUME = "/host/archive.img"
        # The archive volume is encrypted with SECRET_KEY (no separate password).
        app_module.SECRET_KEY = "s3cret"
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
        # Agent was asked for status (host-mount guard), then to mount (with the
        # password) and to unmount.
        actions = [r["action"] for r in self.agent.requests]
        self.assertEqual(actions, ["status", "mount", "unmount"])
        mount_req = self.agent.requests[1]
        self.assertEqual(mount_req["volume"], "/host/archive.img")
        # Encrypted with SECRET_KEY, and asked to auto-create if the volume is absent.
        self.assertEqual(mount_req["password"], "s3cret")
        self.assertTrue(mount_req["create"])
        self.assertEqual(mount_req["size"], app_module.ARCHIVE_SIZE)

    def test_archive_all_includes_videos(self):
        self._auth()
        self._make_image("a.png")
        self._make_image("clip.mp4")
        self._make_image("clip.webm")
        resp = self.client.post("/api/archive", json={"scope": "all"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["archived"], 3)
        self.assertEqual(os.listdir(self.images_dir), [])
        self.assertEqual(self._staged_files(), ["a.png", "clip.mp4", "clip.webm"])

    def test_archive_session_accepts_video(self):
        self._auth()
        self._make_image("clip.mp4")
        resp = self.client.post(
            "/api/archive", json={"scope": "session", "filenames": ["clip.mp4"]}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["archived"], 1)
        self.assertEqual(self._staged_files(), ["001_clip.mp4"])

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
        self._make_image("s2.png")
        resp = self.client.post(
            "/api/archive", json={"scope": "session", "filenames": ["s2.png", "s1.png"]}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["archived"], 2)
        # Session files are prefixed in the caller's order so they sort correctly on disk.
        self.assertEqual(self._staged_files(), ["001_s2.png", "002_s1.png"])
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
        # The volume was still unmounted afterwards (after the status preflight).
        actions = [r["action"] for r in self.agent.requests]
        self.assertEqual(actions, ["status", "mount", "unmount"])

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

    # --- host mount (external access via `m`) --------------------------------

    def test_host_mount_requires_auth(self):
        resp = self.client.post("/api/host-mount")
        self.assertEqual(resp.status_code, 302)

    def test_host_mount_not_configured(self):
        self._auth()
        app_module.ARCHIVE_VOLUME = ""
        resp = self.client.post("/api/host-mount")
        self.assertEqual(resp.status_code, 503)

    def test_host_mount_returns_mountpoint(self):
        self._auth()
        resp = self.client.post("/api/host-mount")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["mountpoint"], "/run/media/private/ben/secure")
        # The agent was asked to host-mount the volume with the SECRET_KEY passphrase.
        req = self.agent.requests[-1]
        self.assertEqual(req["action"], "host-mount")
        self.assertEqual(req["target"], "host")
        self.assertEqual(req["volume"], "/host/archive.img")
        self.assertEqual(req["password"], "s3cret")

    def test_host_unmount(self):
        self._auth()
        resp = self.client.post("/api/host-unmount")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["ok"])
        req = self.agent.requests[-1]
        self.assertEqual(req["action"], "host-unmount")
        self.assertEqual(req["target"], "host")

    def test_host_status_reports_state(self):
        self._auth()
        self.agent.host_mounted = True
        resp = self.client.get("/api/host-status")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["configured"])
        self.assertTrue(data["host_mounted"])

    def test_archive_refused_while_host_mounted(self):
        # While the host holds the volume (via `m`), archiving must refuse rather
        # than mount a second time under it — and must not delete originals.
        self._auth()
        self.agent.host_mounted = True
        self._make_image("a.png")
        resp = self.client.post("/api/archive", json={"scope": "all"})
        self.assertEqual(resp.status_code, 409)
        self.assertIn("m -u", resp.get_json()["error"])
        # Original preserved; the agent was only asked for status (no mount).
        self.assertIn("a.png", os.listdir(self.images_dir))
        self.assertEqual([r["action"] for r in self.agent.requests], ["status"])

    def test_fscheck_refused_while_host_mounted(self):
        # fsck must never run while the host has the volume mounted (e2fsck needs
        # it unmounted). The background job should surface the refusal, not fsck.
        self._auth()
        self.agent.host_mounted = True
        resp = self.client.post("/api/fscheck")
        self.assertEqual(resp.status_code, 200)
        job_id = resp.get_json()["job_id"]
        body = self.client.get(f"/api/progress/{job_id}").get_data(as_text=True)
        self.assertIn("m -u", body)
        # No fsck was sent to the agent (only the status guard).
        self.assertNotIn("fsck", [r["action"] for r in self.agent.requests])


if __name__ == "__main__":
    unittest.main()
