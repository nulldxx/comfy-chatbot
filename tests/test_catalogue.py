import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalogue


class TestLoadServerCatalogue(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        with patch.object(catalogue, "COMFY_WORKFLOW_DIR", Path("/no/such/dir")):
            self.assertEqual(catalogue.load_server_catalogue(), [])

    def test_returns_servers_list(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "servers.json"
            p.write_text(json.dumps({"servers": [{"host": "localhost"}]}))
            with patch.object(catalogue, "COMFY_WORKFLOW_DIR", Path(d)):
                result = catalogue.load_server_catalogue()
        self.assertEqual(result, [{"host": "localhost"}])

    def test_missing_servers_key_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "servers.json").write_text(json.dumps({}))
            with patch.object(catalogue, "COMFY_WORKFLOW_DIR", Path(d)):
                result = catalogue.load_server_catalogue()
        self.assertEqual(result, [])

    def test_malformed_json_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "servers.json").write_text("not json{{{")
            with patch.object(catalogue, "COMFY_WORKFLOW_DIR", Path(d)):
                result = catalogue.load_server_catalogue()
        self.assertEqual(result, [])


class TestLoadLoras(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        with patch.object(catalogue, "COMFY_LORAS_FILE", Path("/no/such/loras.json")):
            self.assertEqual(catalogue.load_loras(), [])

    def test_string_entries_normalised_to_dicts(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "loras.json"
            f.write_text(json.dumps({"loras": ["my-lora"]}))
            with patch.object(catalogue, "COMFY_LORAS_FILE", f):
                result = catalogue.load_loras()
        self.assertEqual(result, [{"name": "my-lora", "strength": 1.0}])

    def test_dict_entries_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "loras.json"
            f.write_text(json.dumps({"loras": [{"name": "my-lora", "strength": 0.7}]}))
            with patch.object(catalogue, "COMFY_LORAS_FILE", f):
                result = catalogue.load_loras()
        self.assertEqual(result, [{"name": "my-lora", "strength": 0.7}])

    def test_malformed_json_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "loras.json"
            f.write_text("oops")
            with patch.object(catalogue, "COMFY_LORAS_FILE", f):
                self.assertEqual(catalogue.load_loras(), [])

    def test_missing_loras_key_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "loras.json"
            f.write_text(json.dumps({}))
            with patch.object(catalogue, "COMFY_LORAS_FILE", f):
                self.assertEqual(catalogue.load_loras(), [])


class TestLoraCatalogueStrength(unittest.TestCase):
    def _patch_loras(self, entries):
        return patch.object(catalogue, "load_loras", return_value=entries)

    def test_returns_strength_as_string(self):
        with self._patch_loras([{"name": "x", "strength": 0.8}]):
            self.assertEqual(catalogue.lora_catalogue_strength("x"), "0.8")

    def test_missing_lora_returns_none(self):
        with self._patch_loras([{"name": "y", "strength": 1.0}]):
            self.assertIsNone(catalogue.lora_catalogue_strength("missing"))

    def test_empty_catalogue_returns_none(self):
        with self._patch_loras([]):
            self.assertIsNone(catalogue.lora_catalogue_strength("x"))


class TestParseLorasFromPrompt(unittest.TestCase):
    def setUp(self):
        # Patch lora_catalogue_strength so strength defaults don't require a file
        self._patcher = patch.object(catalogue, "lora_catalogue_strength", return_value=None)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_no_lora_tags(self):
        clean, loras = catalogue.parse_loras_from_prompt("a cat on a mat")
        self.assertEqual(clean, "a cat on a mat")
        self.assertEqual(loras, [])

    def test_single_lora_with_strength(self):
        clean, loras = catalogue.parse_loras_from_prompt("photo <lora:my-lora:0.8> style")
        self.assertEqual(clean, "photo style")
        self.assertEqual(loras, [("my-lora", "0.8")])

    def test_single_lora_without_strength_defaults_to_1(self):
        clean, loras = catalogue.parse_loras_from_prompt("photo <lora:my-lora> style")
        self.assertEqual(loras[0][1], "1.0")

    def test_strength_from_catalogue(self):
        with patch.object(catalogue, "lora_catalogue_strength", return_value="0.6"):
            clean, loras = catalogue.parse_loras_from_prompt("<lora:cat-lora>")
        self.assertEqual(loras[0][1], "0.6")

    def test_multiple_loras(self):
        clean, loras = catalogue.parse_loras_from_prompt("<lora:a:0.5> text <lora:b:0.9>")
        self.assertEqual(len(loras), 2)
        self.assertEqual(clean, "text")

    def test_collapses_double_spaces(self):
        clean, _ = catalogue.parse_loras_from_prompt("a <lora:x:1.0> b")
        self.assertNotIn("  ", clean)

    def test_case_insensitive(self):
        _, loras = catalogue.parse_loras_from_prompt("<LORA:MY-LORA:0.5>")
        self.assertEqual(loras, [("MY-LORA", "0.5")])


class TestListWorkflowNames(unittest.TestCase):
    def test_returns_sorted_names(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "z.json").write_text("{}")
            (base / "a.json").write_text("{}")
            names = catalogue.list_workflow_names(base)
        self.assertEqual(names, ["a", "z"])

    def test_nested_names_use_forward_slashes(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "sub").mkdir()
            (base / "sub" / "wf.json").write_text("{}")
            names = catalogue.list_workflow_names(base)
        self.assertEqual(names, ["sub/wf"])

    def test_missing_dir_returns_empty(self):
        self.assertEqual(catalogue.list_workflow_names(Path("/no/such")), [])


class TestResolveWorkflow(unittest.TestCase):
    def test_valid_name_returned(self):
        from app import app
        with app.app_context():
            name, err = catalogue.resolve_workflow("flux", ["flux", "sd"], "generation")
        self.assertEqual(name, "flux")
        self.assertIsNone(err)

    def test_unknown_name_returns_error(self):
        from app import app
        with app.app_context():
            name, err = catalogue.resolve_workflow("bad", ["flux", "sd"], "generation")
        self.assertIsNone(name)
        self.assertIsNotNone(err)
        resp, status = err
        self.assertEqual(status, 400)

    def test_none_name_picks_first(self):
        from app import app
        with app.app_context():
            name, err = catalogue.resolve_workflow(None, ["first", "second"], "generation")
        self.assertEqual(name, "first")
        self.assertIsNone(err)

    def test_none_name_empty_list_returns_error(self):
        from app import app
        with app.app_context():
            name, err = catalogue.resolve_workflow(None, [], "generation")
        self.assertIsNone(name)
        self.assertIsNotNone(err)

    def test_strips_json_extension(self):
        from app import app
        with app.app_context():
            name, err = catalogue.resolve_workflow("flux.json", ["flux"], "generation")
        self.assertEqual(name, "flux")
        self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main()
