"""Tests for server_status.py — the ComfyUI/ComfyTray liveness probes and the
remote power control behind /server-status.

``requests`` is patched throughout: these pin the wire contract against
ComfyTray's ApiRoutes.cs (camelCase payload, nulls while stopped, idempotent
start/stop, a 500 carrying the launch failure) without needing either server.
"""
import os
import sys
import unittest
from unittest.mock import patch

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server_status


class _FakeResponse:
    def __init__(self, payload, status_code=200, raises_value_error=False):
        self._payload = payload
        self.status_code = status_code
        self._raises_value_error = raises_value_error

    def json(self):
        if self._raises_value_error:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")


# ComfyTray's exact payloads, from ApiRoutes.cs / MainWindow.Snapshot.
TRAY_RUNNING = {"running": True, "state": "running", "pid": 4312, "port": 8000,
                "uptimeSeconds": 8040.0, "changed": False}
TRAY_STOPPED = {"running": False, "state": "stopped", "pid": None, "port": None,
                "uptimeSeconds": None, "changed": False}


class TestTrayAddress(unittest.TestCase):
    def test_uses_comfy_host_with_tray_port(self):
        """Same host as ComfyUI, the tray's own port — never ComfyUI's."""
        with patch.object(server_status, "COMFY_TRAY_PORT", 8765):
            self.assertEqual(server_status.tray_address("mordor"), "mordor:8765")


class TestProbeComfy(unittest.TestCase):
    def test_reachable_reports_version(self):
        payload = {"system": {"comfyui_version": "0.34.2", "os": "nt"}}
        with patch.object(server_status.requests, "get",
                          return_value=_FakeResponse(payload)) as get:
            result = server_status.probe_comfy("mordor:8000", timeout=1)
        self.assertEqual(result, {"reachable": True, "version": "0.34.2", "error": None})
        self.assertEqual(get.call_args[0][0], "http://mordor:8000/system_stats")

    def test_reachable_without_version_block(self):
        """An older ComfyUI still counts as up; the version is decoration."""
        with patch.object(server_status.requests, "get",
                          return_value=_FakeResponse({})):
            result = server_status.probe_comfy("mordor:8000", timeout=1)
        self.assertTrue(result["reachable"])
        self.assertIsNone(result["version"])

    def test_connection_error_is_an_answer_not_a_raise(self):
        with patch.object(server_status.requests, "get",
                          side_effect=requests.exceptions.ConnectionError("refused")):
            result = server_status.probe_comfy("mordor:8000", timeout=1)
        self.assertEqual(result, {"reachable": False, "version": None,
                                  "error": "connection refused"})

    def test_timeout_is_reported_shortly(self):
        with patch.object(server_status.requests, "get",
                          side_effect=requests.exceptions.ReadTimeout("slow")):
            result = server_status.probe_comfy("mordor:8000", timeout=1)
        self.assertFalse(result["reachable"])
        self.assertEqual(result["error"], "timed out")

    def test_http_error_is_not_reachable(self):
        with patch.object(server_status.requests, "get",
                          return_value=_FakeResponse({}, status_code=500)):
            result = server_status.probe_comfy("mordor:8000", timeout=1)
        self.assertFalse(result["reachable"])

    def test_something_else_on_the_port(self):
        with patch.object(server_status.requests, "get",
                          return_value=_FakeResponse(None, raises_value_error=True)):
            result = server_status.probe_comfy("mordor:8000", timeout=1)
        self.assertFalse(result["reachable"])
        self.assertEqual(result["error"], "not ComfyUI")


class TestProbeTray(unittest.TestCase):
    def test_running_maps_camel_case_to_snake_case(self):
        with patch.object(server_status.requests, "get",
                          return_value=_FakeResponse(TRAY_RUNNING)) as get:
            result = server_status.probe_tray("mordor", timeout=1)
        self.assertEqual(result, {"reachable": True, "running": True, "pid": 4312,
                                  "port": 8000, "uptime_seconds": 8040.0,
                                  "changed": False, "error": None})
        self.assertTrue(get.call_args[0][0].endswith("/api/status"))

    def test_stopped_keeps_nulls_as_none(self):
        """pid/port/uptime are null while stopped; 0 would read as a real pid."""
        with patch.object(server_status.requests, "get",
                          return_value=_FakeResponse(TRAY_STOPPED)):
            result = server_status.probe_tray("mordor", timeout=1)
        self.assertTrue(result["reachable"])
        self.assertFalse(result["running"])
        self.assertIsNone(result["pid"])
        self.assertIsNone(result["port"])
        self.assertIsNone(result["uptime_seconds"])

    def test_no_tray_is_unreachable_not_an_error(self):
        """An unmanaged server: reported, never raised."""
        with patch.object(server_status.requests, "get",
                          side_effect=requests.exceptions.ConnectionError("refused")):
            result = server_status.probe_tray("laptop", timeout=1)
        self.assertFalse(result["reachable"])
        self.assertFalse(result["running"])
        self.assertEqual(result["error"], "connection refused")

    def test_non_dict_payload_is_not_comfytray(self):
        with patch.object(server_status.requests, "get",
                          return_value=_FakeResponse(["nope"])):
            result = server_status.probe_tray("laptop", timeout=1)
        self.assertFalse(result["reachable"])
        self.assertEqual(result["error"], "not ComfyTray")


class TestTrayPower(unittest.TestCase):
    def test_start_posts_to_the_start_route(self):
        started = dict(TRAY_RUNNING, changed=True)
        with patch.object(server_status.requests, "post",
                          return_value=_FakeResponse(started)) as post:
            result = server_status.tray_power("mordor", "start", timeout=1)
        self.assertTrue(post.call_args[0][0].endswith(":8765/api/start"))
        self.assertTrue(result["running"])
        self.assertTrue(result["changed"])

    def test_stop_posts_to_the_stop_route(self):
        with patch.object(server_status.requests, "post",
                          return_value=_FakeResponse(dict(TRAY_STOPPED, changed=True))) as post:
            result = server_status.tray_power("mordor", "stop", timeout=1)
        self.assertTrue(post.call_args[0][0].endswith(":8765/api/stop"))
        self.assertFalse(result["running"])
        self.assertTrue(result["changed"])

    def test_idempotent_start_succeeds_with_changed_false(self):
        """Already running is a 200, not an error — the caller must not treat
        'it was already in that state' as a failure."""
        with patch.object(server_status.requests, "post",
                          return_value=_FakeResponse(TRAY_RUNNING)):
            result = server_status.tray_power("mordor", "start", timeout=1)
        self.assertTrue(result["running"])
        self.assertFalse(result["changed"])

    def test_launch_failure_500_carries_the_trays_message(self):
        body = {"error": "ComfyUI folder not found: C:\\ComfyUI"}
        with patch.object(server_status.requests, "post",
                          return_value=_FakeResponse(body, status_code=500)):
            with self.assertRaises(server_status.TrayError) as ctx:
                server_status.tray_power("mordor", "start", timeout=1)
        self.assertIn("ComfyUI folder not found", str(ctx.exception))

    def test_unexpected_status_without_body_reports_the_code(self):
        with patch.object(server_status.requests, "post",
                          return_value=_FakeResponse(None, status_code=404,
                                                     raises_value_error=True)):
            with self.assertRaises(server_status.TrayError) as ctx:
                server_status.tray_power("mordor", "start", timeout=1)
        self.assertIn("404", str(ctx.exception))

    def test_unreachable_tray_raises_a_readable_error(self):
        with patch.object(server_status.requests, "post",
                          side_effect=requests.exceptions.ConnectionError("refused")):
            with self.assertRaises(server_status.TrayError) as ctx:
                server_status.tray_power("laptop", "start", timeout=1)
        self.assertIn("isn't managed by ComfyTray", str(ctx.exception))

    def test_unknown_action_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            server_status.tray_power("mordor", "restart", timeout=1)


class TestProbeAll(unittest.TestCase):
    def test_one_result_per_entry_with_both_readings(self):
        entries = [
            {"name": "mordor", "host": "mordor", "port": 8000, "os": "windows"},
            {"name": "laptop", "host": "10.0.0.9", "port": 8188, "os": "unix"},
        ]
        with patch.object(server_status, "probe_comfy",
                          side_effect=lambda a, t=None: {"reachable": True, "version": None, "error": None}), \
             patch.object(server_status, "probe_tray",
                          side_effect=lambda h, t=None: server_status._unreachable("connection refused")):
            results = server_status.probe_all(entries, timeout=1)

        self.assertEqual([r["address"] for r in results],
                         ["mordor:8000", "10.0.0.9:8188"])
        self.assertEqual([r["name"] for r in results], ["mordor", "laptop"])
        self.assertTrue(all(r["comfy"]["reachable"] for r in results))
        self.assertTrue(all(not r["tray"]["reachable"] for r in results))

    def test_empty_catalogue_needs_no_pool(self):
        self.assertEqual(server_status.probe_all([]), [])

    def test_probes_run_concurrently(self):
        """N servers must cost roughly one timeout, not N — a single dead host
        should not make the panel wait for every other one in turn."""
        import threading
        import time

        barrier = threading.Barrier(3, timeout=5)

        def slow_probe(_a, _t=None):
            barrier.wait()  # raises BrokenBarrierError unless all three overlap
            return {"reachable": False, "version": None, "error": "timed out"}

        entries = [{"name": str(i), "host": f"h{i}", "port": 8000, "os": "unix"}
                   for i in range(3)]
        with patch.object(server_status, "probe_comfy", side_effect=slow_probe), \
             patch.object(server_status, "probe_tray",
                          side_effect=lambda h, t=None: server_status._unreachable("x")):
            start = time.monotonic()
            results = server_status.probe_all(entries, timeout=1)
        self.assertEqual(len(results), 3)
        self.assertLess(time.monotonic() - start, 5)


if __name__ == "__main__":
    unittest.main()
