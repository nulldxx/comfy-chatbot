import os
import sys
import json
import socket
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import image_store as image_store_module
from app import app, output_storage_error
import agent_client


class FakeAgent:
    """Throwaway Unix-socket server emulating archive-agent for the output-volume
    flow. Records requests, replies {"ok": true}, and (unless skip_marker) drops
    the mount marker into `images_dir` like the real agent does on mount."""

    def __init__(self, sock_path, images_dir):
        self.sock_path = sock_path
        self.images_dir = images_dir
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
                    if not self.skip_marker:
                        with open(os.path.join(self.images_dir, ".comfy-archive"),
                                  "w", encoding="utf-8") as fh:
                            fh.write("comfy-archive\n")
                conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)
        self._srv.close()


class TestOutputGuard(unittest.TestCase):
    """The generation routes must refuse to start when output encryption is
    enabled but the encrypted volume isn't mounted (marker missing)."""

    def setUp(self):
        app.testing = True
        self.client = app.test_client()
        self.tmp = tempfile.mkdtemp()
        self.images_dir = os.path.join(self.tmp, "output")
        os.makedirs(self.images_dir)
        self._orig = {
            "IMAGES_DIR": app_module.IMAGES_DIR,
            "OUTPUT_VOLUME": app_module.OUTPUT_VOLUME,
        }
        self._orig_image_store = {
            "IMAGES_DIR": image_store_module.IMAGES_DIR,
            "OUTPUT_VOLUME": image_store_module.OUTPUT_VOLUME,
        }
        app_module.IMAGES_DIR = Path(self.images_dir)
        app_module.OUTPUT_VOLUME = "/host/output.luks"
        image_store_module.IMAGES_DIR = Path(self.images_dir)
        image_store_module.OUTPUT_VOLUME = "/host/output.luks"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(app_module, k, v)
        for k, v in self._orig_image_store.items():
            setattr(image_store_module, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _auth(self):
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True

    def _marker(self):
        return os.path.join(self.images_dir, app_module.OUTPUT_MARKER)

    def test_helper_ok_when_disabled(self):
        app_module.OUTPUT_VOLUME = ""
        image_store_module.OUTPUT_VOLUME = ""
        with app.app_context():
            self.assertIsNone(output_storage_error())

    def test_helper_ok_when_mounted(self):
        open(self._marker(), "w").close()
        with app.app_context():
            self.assertIsNone(output_storage_error())

    def test_helper_errors_when_unmounted(self):
        with app.app_context():
            result = output_storage_error()
        self.assertIsNotNone(result)
        assert result is not None  # narrow for type-checkers
        self.assertEqual(result[1], 503)

    def test_generate_refused_without_marker(self):
        # Enabled + no marker => 503 before any generation thread is spawned.
        self._auth()
        resp = self.client.post("/api/generate", json={"prompt": "a cat"})
        self.assertEqual(resp.status_code, 503)

    def test_generate_allowed_when_disabled(self):
        # With encryption off the guard never blocks (route proceeds past it).
        app_module.OUTPUT_VOLUME = ""
        image_store_module.OUTPUT_VOLUME = ""
        with app.app_context():
            self.assertIsNone(output_storage_error())


class TestAgentClient(unittest.TestCase):
    """agent_client's mount-output / unmount-output CLI helpers."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.images_dir = os.path.join(self.tmp, "output")
        os.makedirs(self.images_dir)
        self.sock_path = os.path.join(self.tmp, "agent.sock")
        self.agent = FakeAgent(self.sock_path, self.images_dir)
        self.agent.start()
        self._env = {
            "ARCHIVE_AGENT_SOCKET": self.sock_path,
            "COMFY_OUTPUT_DIR": self.images_dir,
            "OUTPUT_VOLUME": "/host/output.luks",
            "OUTPUT_PASSWORD": "s3cret",
            "OUTPUT_SIZE": "5G",
        }
        self._saved = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.agent.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mount_output_disabled_is_noop(self):
        os.environ["OUTPUT_VOLUME"] = ""
        self.assertEqual(agent_client._mount_output(), 0)
        self.assertEqual(self.agent.requests, [])

    def test_mount_output_creates_and_mounts(self):
        rc = agent_client._mount_output()
        self.assertEqual(rc, 0)
        req = self.agent.requests[0]
        self.assertEqual(req["action"], "mount")
        self.assertEqual(req["target"], "output")
        self.assertTrue(req["create"])
        self.assertEqual(req["volume"], "/host/output.luks")
        self.assertEqual(req["password"], "s3cret")
        self.assertEqual(req["size"], "5G")

    def test_mount_output_password_falls_back_to_secret_key(self):
        os.environ.pop("OUTPUT_PASSWORD", None)
        os.environ["SECRET_KEY"] = "session-secret"
        try:
            self.assertEqual(agent_client._mount_output(), 0)
            self.assertEqual(self.agent.requests[0]["password"], "session-secret")
        finally:
            os.environ.pop("SECRET_KEY", None)

    def test_mount_output_fails_without_marker(self):
        self.agent.skip_marker = True
        self.assertEqual(agent_client._mount_output(), 1)

    def test_unmount_output(self):
        rc = agent_client._unmount_output()
        self.assertEqual(rc, 0)
        req = self.agent.requests[0]
        self.assertEqual(req["action"], "unmount")
        self.assertEqual(req["target"], "output")
        self.assertEqual(req["volume"], "/host/output.luks")


class TestAgentClientSend(unittest.TestCase):
    """Tests for the low-level send() helper and error paths in mount/unmount."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_send_raises_on_bad_socket(self):
        with self.assertRaises(RuntimeError):
            agent_client.send({"action": "ping"}, "/no/such/socket.sock", timeout=1.0)

    def _start_server(self, response_bytes):
        """Start a trivial Unix-socket server that sends `response_bytes` then closes."""
        sock_path = os.path.join(self.tmp, "srv.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock_path)
        srv.listen(1)
        srv.settimeout(2.0)

        def _serve():
            try:
                conn, _ = srv.accept()
                conn.recv(4096)
                if response_bytes:
                    conn.sendall(response_bytes)
                conn.close()
            except Exception:
                pass
            finally:
                srv.close()

        threading.Thread(target=_serve, daemon=True).start()
        return sock_path

    def test_send_raises_on_empty_response(self):
        sock_path = self._start_server(b"")
        import time; time.sleep(0.05)
        with self.assertRaises(RuntimeError):
            agent_client.send({"action": "ping"}, sock_path, timeout=2.0)

    def test_send_raises_on_malformed_json(self):
        sock_path = self._start_server(b"not json\n")
        import time; time.sleep(0.05)
        with self.assertRaises(RuntimeError):
            agent_client.send({"action": "ping"}, sock_path, timeout=2.0)


class TestMountOutputErrorPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.images_dir = os.path.join(self.tmp, "output")
        os.makedirs(self.images_dir)
        self._saved = {}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _setenv(self, **kwargs):
        for k, v in kwargs.items():
            self._saved.setdefault(k, os.environ.get(k))
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_no_password_returns_error(self):
        self._setenv(OUTPUT_VOLUME="/host/out.luks", OUTPUT_PASSWORD=None, SECRET_KEY=None)
        self.assertEqual(agent_client._mount_output(), 1)

    def test_send_failure_returns_error(self):
        self._setenv(
            OUTPUT_VOLUME="/host/out.luks",
            OUTPUT_PASSWORD="pw",
            ARCHIVE_AGENT_SOCKET="/no/such/socket.sock",
            COMFY_OUTPUT_DIR=self.images_dir,
        )
        self.assertEqual(agent_client._mount_output(), 1)

    def test_agent_not_ok_returns_error(self):
        # Set up a fake agent that returns {"ok": false}
        sock_path = os.path.join(self.tmp, "agent.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock_path)
        srv.listen(1)
        srv.settimeout(2.0)

        def _serve():
            try:
                conn, _ = srv.accept()
                conn.recv(4096)
                conn.sendall((json.dumps({"ok": False, "error": "no space"}) + "\n").encode())
                conn.close()
            except Exception:
                pass
            finally:
                srv.close()

        threading.Thread(target=_serve, daemon=True).start()
        import time; time.sleep(0.05)

        self._setenv(
            OUTPUT_VOLUME="/host/out.luks",
            OUTPUT_PASSWORD="pw",
            ARCHIVE_AGENT_SOCKET=sock_path,
            COMFY_OUTPUT_DIR=self.images_dir,
        )
        self.assertEqual(agent_client._mount_output(), 1)


class TestUnmountOutputErrorPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = {}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _setenv(self, **kwargs):
        for k, v in kwargs.items():
            self._saved.setdefault(k, os.environ.get(k))
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_disabled_returns_zero(self):
        self._setenv(OUTPUT_VOLUME=None)
        self.assertEqual(agent_client._unmount_output(), 0)

    def test_send_failure_returns_error(self):
        self._setenv(OUTPUT_VOLUME="/host/out.luks", ARCHIVE_AGENT_SOCKET="/no/such/socket.sock")
        self.assertEqual(agent_client._unmount_output(), 1)

    def test_agent_not_ok_returns_error(self):
        sock_path = os.path.join(self.tmp, "agent.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock_path)
        srv.listen(1)
        srv.settimeout(2.0)

        def _serve():
            try:
                conn, _ = srv.accept()
                conn.recv(4096)
                conn.sendall((json.dumps({"ok": False, "error": "busy"}) + "\n").encode())
                conn.close()
            except Exception:
                pass
            finally:
                srv.close()

        threading.Thread(target=_serve, daemon=True).start()
        import time; time.sleep(0.05)

        self._setenv(OUTPUT_VOLUME="/host/out.luks", ARCHIVE_AGENT_SOCKET=sock_path)
        self.assertEqual(agent_client._unmount_output(), 1)


class TestAgentClientMain(unittest.TestCase):
    def test_mount_output_command(self):
        # Just verify main() dispatches to _mount_output; patch the function.
        with patch.object(agent_client, "_mount_output", return_value=0) as m:
            rc = agent_client.main(["agent_client", "mount-output"])
        self.assertEqual(rc, 0)
        m.assert_called_once()

    def test_unmount_output_command(self):
        with patch.object(agent_client, "_unmount_output", return_value=0) as m:
            rc = agent_client.main(["agent_client", "unmount-output"])
        self.assertEqual(rc, 0)
        m.assert_called_once()

    def test_unknown_command_returns_2(self):
        rc = agent_client.main(["agent_client", "bad-command"])
        self.assertEqual(rc, 2)

    def test_no_command_returns_2(self):
        rc = agent_client.main(["agent_client"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
