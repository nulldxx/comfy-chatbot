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
        with patch.object(catalogue, "COMFY_LORAS_FILE", Path("/no/such/loras-new.json")):
            self.assertEqual(catalogue.load_loras(), [])

    def test_new_format_parsed_correctly(self):
        data = {"z-image/known/zit-ljr": {"active_triggers": "ljr", "suggested_strength": "0.8"}}
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "loras-new.json"
            f.write_text(json.dumps(data))
            with patch.object(catalogue, "COMFY_LORAS_FILE", f):
                result = catalogue.load_loras()
        self.assertEqual(result, [{"name": "z-image/known/zit-ljr", "strength": 0.8, "triggers": "ljr"}])

    def test_null_strength_defaults_to_0_8(self):
        data = {"my-lora": {"active_triggers": "", "suggested_strength": None}}
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "loras-new.json"
            f.write_text(json.dumps(data))
            with patch.object(catalogue, "COMFY_LORAS_FILE", f):
                result = catalogue.load_loras()
        self.assertEqual(result[0]["strength"], 0.8)

    def test_triggers_included(self):
        data = {"chroma/style/chroma-80s-fantasy-movie": {
            "active_triggers": "ArsMovieStill, 80s Fantasy Movie Still",
            "suggested_strength": None,
        }}
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "loras-new.json"
            f.write_text(json.dumps(data))
            with patch.object(catalogue, "COMFY_LORAS_FILE", f):
                result = catalogue.load_loras()
        self.assertEqual(result[0]["triggers"], "ArsMovieStill, 80s Fantasy Movie Still")

    def test_empty_triggers_becomes_empty_string(self):
        data = {"my-lora": {"active_triggers": "", "suggested_strength": "0.75"}}
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "loras-new.json"
            f.write_text(json.dumps(data))
            with patch.object(catalogue, "COMFY_LORAS_FILE", f):
                result = catalogue.load_loras()
        self.assertEqual(result[0]["triggers"], "")

    def test_malformed_json_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "loras-new.json"
            f.write_text("oops")
            with patch.object(catalogue, "COMFY_LORAS_FILE", f):
                self.assertEqual(catalogue.load_loras(), [])

    def test_empty_object_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "loras-new.json"
            f.write_text(json.dumps({}))
            with patch.object(catalogue, "COMFY_LORAS_FILE", f):
                self.assertEqual(catalogue.load_loras(), [])

    def test_range_strength_uses_average(self):
        data = {"my-lora": {"active_triggers": "", "suggested_strength": "0.8-1.2"}}
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "loras-new.json"
            f.write_text(json.dumps(data))
            with patch.object(catalogue, "COMFY_LORAS_FILE", f):
                result = catalogue.load_loras()
        self.assertAlmostEqual(result[0]["strength"], 1.0)

    def test_range_strength_does_not_wipe_catalogue(self):
        # A range value used to raise ValueError and blow away every entry.
        data = {
            "ranged": {"suggested_strength": "0.5-0.9"},
            "plain": {"suggested_strength": "0.7"},
        }
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "loras-new.json"
            f.write_text(json.dumps(data))
            with patch.object(catalogue, "COMFY_LORAS_FILE", f):
                result = catalogue.load_loras()
        self.assertEqual(len(result), 2)


class TestParseStrength(unittest.TestCase):
    def test_plain_float(self):
        self.assertEqual(catalogue.parse_strength(0.6), 0.6)

    def test_numeric_string(self):
        self.assertEqual(catalogue.parse_strength("0.75"), 0.75)

    def test_range_averages(self):
        self.assertAlmostEqual(catalogue.parse_strength("0.8-1.2"), 1.0)

    def test_range_with_spaces(self):
        self.assertAlmostEqual(catalogue.parse_strength("0.8 - 1.2"), 1.0)

    def test_none_uses_default(self):
        self.assertEqual(catalogue.parse_strength(None), 0.8)
        self.assertEqual(catalogue.parse_strength(None, default=0.5), 0.5)

    def test_empty_string_uses_default(self):
        self.assertEqual(catalogue.parse_strength(""), 0.8)

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            catalogue.parse_strength("not-a-number")


class TestLoadLorasResult(unittest.TestCase):
    def test_missing_file_no_error(self):
        with patch.object(catalogue, "COMFY_LORAS_FILE", Path("/no/such/loras-new.json")):
            result = catalogue.load_loras_result()
        self.assertEqual(result, {"loras": [], "error": None})

    def test_malformed_json_reports_error(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "loras-new.json"
            f.write_text("oops")
            with patch.object(catalogue, "COMFY_LORAS_FILE", f):
                result = catalogue.load_loras_result()
        self.assertEqual(result["loras"], [])
        self.assertIsNotNone(result["error"])

    def test_non_object_reports_error(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "loras-new.json"
            f.write_text(json.dumps([1, 2, 3]))
            with patch.object(catalogue, "COMFY_LORAS_FILE", f):
                result = catalogue.load_loras_result()
        self.assertEqual(result["loras"], [])
        self.assertIsNotNone(result["error"])

    def test_bad_entry_skipped_and_reported(self):
        data = {
            "good": {"suggested_strength": "0.7"},
            "bad": {"suggested_strength": "wat"},
        }
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "loras-new.json"
            f.write_text(json.dumps(data))
            with patch.object(catalogue, "COMFY_LORAS_FILE", f):
                result = catalogue.load_loras_result()
        self.assertEqual([l["name"] for l in result["loras"]], ["good"])
        self.assertIn("bad", result["error"])

    def test_valid_file_no_error(self):
        data = {"my-lora": {"active_triggers": "t", "suggested_strength": "0.8-1.2"}}
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "loras-new.json"
            f.write_text(json.dumps(data))
            with patch.object(catalogue, "COMFY_LORAS_FILE", f):
                result = catalogue.load_loras_result()
        self.assertIsNone(result["error"])
        self.assertAlmostEqual(result["loras"][0]["strength"], 1.0)


class TestLoraCatalogueStrength(unittest.TestCase):
    def _patch_loras(self, entries):
        return patch.object(catalogue, "load_loras", return_value=entries)

    def test_returns_strength_as_string(self):
        with self._patch_loras([{"name": "x", "strength": 0.8, "triggers": "ljr"}]):
            self.assertEqual(catalogue.lora_catalogue_strength("x"), "0.8")

    def test_missing_lora_returns_none(self):
        with self._patch_loras([{"name": "y", "strength": 0.8, "triggers": ""}]):
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


class TestListWorkflowWrappers(unittest.TestCase):
    """The four thin wrappers each delegate to list_workflow_names — just confirm
    they call through to the right directory and return a list."""

    def _make_workflow(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "wf.json").write_text("{}")

    def test_list_facedetailer_workflows(self):
        with tempfile.TemporaryDirectory() as d:
            self._make_workflow(Path(d) / "facedetailer")
            with patch.object(catalogue, "COMFY_FACEDETAILER_DIR", Path(d) / "facedetailer"):
                result = catalogue.list_facedetailer_workflows()
        self.assertEqual(result, ["wf"])

    def test_list_upscaler_workflows(self):
        with tempfile.TemporaryDirectory() as d:
            self._make_workflow(Path(d) / "upscaler")
            with patch.object(catalogue, "COMFY_UPSCALER_DIR", Path(d) / "upscaler"):
                result = catalogue.list_upscaler_workflows()
        self.assertEqual(result, ["wf"])

    def test_list_image2image_workflows(self):
        with tempfile.TemporaryDirectory() as d:
            self._make_workflow(Path(d) / "i2i")
            with patch.object(catalogue, "COMFY_IMAGE2IMAGE_DIR", Path(d) / "i2i"):
                result = catalogue.list_image2image_workflows()
        self.assertEqual(result, ["wf"])

    def test_list_inpainting_workflows(self):
        with tempfile.TemporaryDirectory() as d:
            self._make_workflow(Path(d) / "inpaint")
            with patch.object(catalogue, "COMFY_INPAINTING_DIR", Path(d) / "inpaint"):
                result = catalogue.list_inpainting_workflows()
        self.assertEqual(result, ["wf"])


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

    def test_model_variant_suffix_is_validated_on_the_base_and_kept(self):
        from app import app
        with app.app_context():
            name, err = catalogue.resolve_workflow("flux@fp16", ["flux", "sd"], "generation")
        self.assertEqual(name, "flux@fp16")
        self.assertIsNone(err)

    def test_unknown_base_with_a_suffix_still_returns_error(self):
        from app import app
        with app.app_context():
            name, err = catalogue.resolve_workflow("bad@fp16", ["flux"], "generation")
        self.assertIsNone(name)
        self.assertEqual(err[1], 400)

    def test_a_literal_name_containing_the_separator_wins(self):
        from app import app
        with app.app_context():
            name, err = catalogue.resolve_workflow("odd@name", ["odd@name"], "generation")
        self.assertEqual(name, "odd@name")
        self.assertIsNone(err)


class TestResolveWorkflowPath(unittest.TestCase):
    def _base(self, d):
        base = Path(d)
        (base / "sub").mkdir()
        (base / "wf.json").write_text("{}")
        (base / "sub" / "nested.json").write_text("{}")
        return base

    def test_resolves_plain_and_nested_names(self):
        with tempfile.TemporaryDirectory() as d:
            base = self._base(d)
            path, variant = catalogue.resolve_workflow_path(base, "wf")
            self.assertEqual(path, base / "wf.json")
            self.assertIsNone(variant)
            path, _ = catalogue.resolve_workflow_path(base, "sub/nested")
            self.assertEqual(path, base / "sub" / "nested.json")

    def test_splits_the_model_variant_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            base = self._base(d)
            path, variant = catalogue.resolve_workflow_path(base, "wf@fp16")
            self.assertEqual(path, base / "wf.json")
            self.assertEqual(variant, "fp16")

    def test_a_filename_containing_the_separator_still_resolves(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "odd@name.json").write_text("{}")
            path, variant = catalogue.resolve_workflow_path(base, "odd@name")
            self.assertEqual(path, base / "odd@name.json")
            self.assertIsNone(variant)

    def test_traversal_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            base = self._base(d)
            (base.parent / "outside.json").write_text("{}")
            with self.assertRaises(FileNotFoundError):
                catalogue.resolve_workflow_path(base, "../outside")

    def test_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                catalogue.resolve_workflow_path(Path(d), "nope")


class TestListWorkflowVariants(unittest.TestCase):
    TEMPLATE = json.dumps({
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "a_int8.safetensors, a_fp16.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "<PROMPT>"}},
        "3": {"class_type": "KSampler", "inputs": {"denoise": "<DENOISE>"}},
    }).replace('"<DENOISE>"', "<DENOISE>")

    def test_lists_the_alternates_of_a_template_with_placeholders(self):
        # The raw template is not valid JSON on its own — the dummy-value substitution
        # is what makes it parseable, exactly as at startup validation.
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "wf.json").write_text(self.TEMPLATE)
            self.assertEqual(catalogue.list_workflow_variants(base, "wf"),
                             ["a_int8", "a_fp16"])

    def test_template_without_alternates_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "wf.json").write_text(
                '{"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "only.safetensors"}}}')
            self.assertEqual(catalogue.list_workflow_variants(base, "wf"), [])

    def test_ui_format_export_returns_empty(self):
        # UI-format graphs carry no class_type, so nothing is introspectable — the
        # picker just shows no extra level rather than erroring.
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "wf.json").write_text('{"nodes": [{"type": "UNETLoader"}], "links": []}')
            self.assertEqual(catalogue.list_workflow_variants(base, "wf"), [])

    def test_unparseable_missing_and_escaping_names_return_empty(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "broken.json").write_text("{not json")
            self.assertEqual(catalogue.list_workflow_variants(base, "broken"), [])
            self.assertEqual(catalogue.list_workflow_variants(base, "missing"), [])
            self.assertEqual(catalogue.list_workflow_variants(base, "../outside"), [])

    def test_mismatched_alternate_lists_return_empty(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "wf.json").write_text(json.dumps({
                "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "a.safetensors, b.safetensors"}},
                "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "c.safetensors, d.safetensors, e.safetensors"}},
            }))
            self.assertEqual(catalogue.list_workflow_variants(base, "wf"), [])


if __name__ == "__main__":
    unittest.main()
