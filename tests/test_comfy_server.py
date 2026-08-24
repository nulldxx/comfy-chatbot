import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ComfyServer as comfy_module
from ComfyServer import ComfyServer


def _response(payload):
    r = Mock()
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


class TestGetNodeOutputTypes(unittest.TestCase):
    """Declared node output types, used to tell a loader's IMAGE and AUDIO links apart."""

    def setUp(self):
        comfy_module._OBJECT_INFO_CACHE.clear()

    tearDown = setUp

    def test_parses_output_list(self):
        payload = {"VHS_LoadVideo": {"output": ["IMAGE", "MASK", "AUDIO", "VHS_VIDEOINFO"]}}
        with patch.object(comfy_module.requests, "get",
                          return_value=_response(payload)) as get:
            types = ComfyServer("host:1").get_node_output_types("VHS_LoadVideo")
        self.assertEqual(types, ["IMAGE", "MASK", "AUDIO", "VHS_VIDEOINFO"])
        self.assertIn("/object_info/VHS_LoadVideo", get.call_args[0][0])

    def test_unknown_class_returns_empty(self):
        with patch.object(comfy_module.requests, "get", return_value=_response({})):
            self.assertEqual(ComfyServer("host:1").get_node_output_types("Nope"), [])

    def test_combo_outputs_normalised(self):
        payload = {"N": {"output": ["IMAGE", ["a", "b"]]}}
        with patch.object(comfy_module.requests, "get", return_value=_response(payload)):
            self.assertEqual(ComfyServer("host:1").get_node_output_types("N"),
                             ["IMAGE", "COMBO"])

    def test_cached_per_server_and_class(self):
        payload = {"N": {"output": ["IMAGE"]}}
        with patch.object(comfy_module.requests, "get",
                          return_value=_response(payload)) as get:
            # A ComfyServer is constructed per job, so the cache must not be per-instance.
            ComfyServer("host:1").get_node_output_types("N")
            ComfyServer("host:1").get_node_output_types("N")
            self.assertEqual(get.call_count, 1)
            ComfyServer("host:2").get_node_output_types("N")
            self.assertEqual(get.call_count, 2)
            ComfyServer("host:1").get_node_output_types("Other")
            self.assertEqual(get.call_count, 3)

    def test_empty_result_not_cached(self):
        # A transient empty answer (ComfyUI restarting) must not be poisoned in.
        with patch.object(comfy_module.requests, "get",
                          return_value=_response({})) as get:
            ComfyServer("host:1").get_node_output_types("N")
            ComfyServer("host:1").get_node_output_types("N")
            self.assertEqual(get.call_count, 2)

    def test_transport_error_propagates(self):
        with patch.object(comfy_module.requests, "get",
                          side_effect=comfy_module.requests.exceptions.ConnectionError("x")):
            with self.assertRaises(comfy_module.requests.exceptions.ConnectionError):
                ComfyServer("host:1").get_node_output_types("N")


if __name__ == "__main__":
    unittest.main()
